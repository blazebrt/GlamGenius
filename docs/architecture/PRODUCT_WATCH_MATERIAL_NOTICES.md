# Product Watch — material notices for a pack the customer chose to watch (Step 12C)

Step 12C is the reaction layer on top of Step 12A and Step 12B. A signed-in
customer explicitly watches one exact confirmed pack. When a materially relevant,
customer-publishable fact becomes available *after* they started watching, the
existing notification cycle may surface at most one governed, deduplicated notice.

It is not a truth engine. The division of labour is exact:

```text
Existing authorities determine truth.
Product Watch determines whether the customer asked to hear about it.
Notification infrastructure determines whether and how it may be delivered.
```

No model decides anything here: not whether something changed, not whether it is
material, not whether a record applies, not whether a product became safe. Code:
`backend/app/domains/product/watch.py`. Tests:
`backend/tests/test_step12c_product_watch.py` and
`frontend/src/__tests__/productWatch*.test.tsx`.

## The rule that matters most: evidence is inherited

A notification is itself a customer-facing claim. So:

> If Step 12A or Step 12B is not allowed to publish the underlying fact on the
> Product Result screen, Step 12C is not allowed to publish that fact in a push
> notification either.

Product Watch reads the same governed envelopes the product screen reads — the
official-records envelope with its Step 12B `regulatory_change` block, and the
Step 12A projection through `label_evidence.comparison_is_publishable`. It never
reads the internal fields behind them: not `LabelSnapshot.changed_fields`, not
`content_fingerprint`, not version numbers as a proxy for change, not revision
payloads, not `latest_revision`, not status fields. A notification cannot become
a side door around an evidence gate.

## Relationship to Step 12A and Step 12B

| | Step 12A | Step 12B | Step 12C |
| --- | --- | --- | --- |
| Question | Did a confirmed label observation change? | Did an official record's own history change? | Did the customer ask, and is there something new to tell them? |
| Kind of state | Derived on read | Derived on read | One small customer-owned table |
| Publishes | A governed envelope on Product Result | A governed envelope on Product Result | At most one outbox row per cycle |

Step 12C consumes both and overrides neither. Their docs:
[`LABEL_CHANGE_AUTHORITY.md`](LABEL_CHANGE_AUTHORITY.md),
[`REGULATORY_CHANGE_AUTHORITY.md`](REGULATORY_CHANGE_AUTHORITY.md).

## Explicit watch consent

A watch begins only when the customer taps **Watch verified changes** on the
verdict screen (`PUT /api/v2/scan/verdict/{barcode}/watch`). It is never inferred
from a scan, a label confirmation, a shelf item, a purchase, a Decision Memory
row, repeated views, inventory ownership, community reports or Open Food Facts
data. Those establish context; they do not establish consent. A structural test
proves that the only code in `backend/app` that constructs a `ProductWatch` row
is the watch service itself.

Stopping is one tap (**Stop watching**, `DELETE …/watch`) and needs no device.

Watching and being told are different consents, and so is the operating system's
permission. The Watch button never asks for notification permission and never
turns native push on; it only reports the current delivery state so the screen can
say, neutrally, that notifications for watched products are off.

## Exact pack anchoring

A barcode is not a pack, and neither is a label version. Batch is deliberately
outside the label content fingerprint, so one label version can be shared by
several packs with different lots — including a stranger's. The watch is therefore
anchored to two things:

1. **The capture** (`anchor_scan_event_id`): this account's own confirmed label
   capture, on a device this account has claimed. Its label facts are the only
   facts ever matched against official records — never the shared snapshot's,
   which may carry another person's lot.
2. **The label version** (`anchor_label_snapshot_id`, `anchor_label_version`): the
   version that speaks for that capture, resolved by the existing governed
   resolver `resolve_current_pack_label_snapshot`, not "the newest snapshot
   anyone published".

Watchability uses three existing authorities and adds none:
`pack_context.current_pack` (the device's newest scan must be a genuine confirmed
capture), the Step 10A ownership rule (device claimed by this account, capture
made by this account), and the current-pack snapshot resolver. The request
contract is `{label_version}` only, as a compare-and-set against the pack the
device proves; the server resolves everything else. With no confirmed
account-bound pack the answer is always `confirmed_pack_required` — one code for
every way of lacking a pack, so the refusal does not reveal whose capture is on a
device. There is no fallback to Open Food Facts.

Every time a stored anchor is used it is re-proven: the capture must still be a
confirmed capture owned by the account for this barcode, and the snapshot must
still be this barcode at the anchored version. A capture whose facts were
withdrawn by an account deletion stops being a capture, and the watch goes quiet
(`product_watch_anchor_invalid reason=…` is logged).

Only the device-capture path is implemented. `InventoryProductLink` was audited as
an alternative anchor and not used: a shelf link records ownership of a label
version, not a capture with a lot, so it cannot supply the exact facts the
official-records matcher needs.

## Baseline semantics

Creating a watch sends nothing. At creation — and at every explicit re-anchor or
re-activation — Product Watch evaluates the current state and records it as
baseline:

- every official record that currently matches this exact pack, with the ledger
  head revision Step 12B validates for it (`validated_revision_heads`);
- the newest label version that already exists for the barcode.

Only what becomes available after the baseline can ever produce a notice. The
baseline lives in `notice_cursor`, a strictly validated JSON document:

```json
{"v": 1,
 "records": {"<FSSAI Recall Id>": {"revisions": [1], "baseline_unknown": false}},
 "label": {"baseline_version": 1, "notified_versions": []}}
```

A malformed cursor is never repaired into something plausible; the watch is
skipped and `product_watch_cursor_invalid` is logged. If a record's ledger did
not survive Step 12B's checks when it became known, it is stored with
`baseline_unknown: true` and never produces a change notice for that anchor,
because no later revision can be told apart from one that already existed.

## Material notice classes

Exactly three, and nothing else is material:

| Class | Key prefix | When | Copy (keyed in `notification_strings.py`) |
| --- | --- | --- | --- |
| A. Record match | `pw:a:` | An exact official record matches the watched pack and was not in the baseline | "An official record matches a product you watch" / "Open the product to review what FSSAI currently lists." |
| B. Regulatory change | `pw:b:` | Step 12B publishes `status = changed` for a known record, naming a revision newer than every one already known | "An official record for a watched product changed" / "Open the product to review the sourced update." |
| C. Label change | `pw:c:` | Step 12A publishes a `changed` comparison for a version newer than the baseline | "Verified pack information changed" / "Open the product to review the sourced difference." |

Materiality is structural. There is no importance score, and nothing ranks one
recall reason, status, ingredient or formula change above another.

Push is an attention surface, not the evidence surface. No notice carries a raw
value — no status, lot, licence, reason, ingredient or before/after. The detail
stays on Product Result, beside its source. None of the copy uses: dangerous,
unsafe, safe, cleared, warning, urgent, improved, worse, reformulated, fixed,
healthier, new recall, newly, just recalled.

## What works today, and what is dormant

- **Class A works today.** The official-records envelope is published whenever the
  matcher resolves an exact match with an openable official source, so a record
  that begins to match a watched pack after the baseline produces a current-state
  notice.
- **Class B is dormant.** Step 12B's publication gate is closed in production:
  `revision_source()` returns `None`, so every `regulatory_change` block is
  `unavailable`. Product Watch therefore produces no regulatory-change notice. The
  far-side wiring is proven with the gate forced open in test scope only.
- **Class C is dormant twice over.** Step 12A's publication gate is closed, and in
  addition the production worker supplies no label pair at all. Choosing one in the
  background would mean picking a label observation this account's device did not
  make and presenting it as news about their pack, which Step 12A's current-pack
  rule forbids. The consumer (`notices_for(label_pair=…)`) exists and is tested
  against real label chains with the gate forced open; the production selector is
  deliberately not built. Correctly sending zero formula notices is better than
  sending one based on somebody else's pack.

Nothing is fabricated to make Product Watch produce more notices.

## Official-record current-match semantics

The watch uses `official_records_envelope` — the verdict route's own call — with
the anchor capture's facts. The same exact licence, meaningful exact lot, conflict
handling, ambiguity withholding and OFF isolation apply; there is no second
matcher. A class-A notice additionally requires the record's own `source_url` to be
`https://` on the official FSSAI host, so the destination screen can open the
register page the notice rests on.

A class-A notice is a current-state statement ("a current official record matches
this watched pack"), never a historical-change claim. Our database observing a
record later does not prove the regulator published it later, so the copy never
says new, newly or just.

First observation is never a change: Step 12A's `first_observed_version` and Step
12B's `first_observed_record` never produce a class B or C notice. A record first
seen after the baseline is a class-A match, which is a different kind of notice.

## Omission semantics

If an official record disappears from a later export, or a later revision names a
different lot so it no longer matches: nothing happens. No "cleared", "resolved",
"safe now", "recall ended" or "no longer affected". Source omission remains
non-evidence. Ambiguous matching produces nothing. Corrupt history produces
nothing. Unavailable evidence produces nothing — and unavailable is not "nothing
changed", so the cursor does not move.

## Cursor and dedupe semantics

Identity is semantic, never a timestamp. A notice's identity is the canonical JSON
of `{kind, barcode, recall_id, revision, label_version}`; its outbox key is
`pw:<class>:<sha256 prefix>`, within the outbox's 64-character key. Two
evaluations of unchanged state produce the same notice and the same key.

The cursor moves only when the outbox records a durable decision, and in the same
transaction as that decision's row, so a rollback loses both together:

| Outbox decision | Cursor |
| --- | --- |
| queued / sending / provider accepted / provider failed | Advances — the outbox owns what happens next |
| suppressed: notifications disabled, or topic off (explicit opt-out) | Advances — the customer decided |
| suppressed: quiet hours, daily cap (temporary) | Stays — the event is eligible on a later local day |

The outbox's own per-day dedup (`dedup_hash(account, plan_date, key, title)`)
bounds a held event to at most one decision row per local day, so "eligible later"
never becomes infinite rows. A provider failure is final for that event: the
existing worker contract governs provider failures and Product Watch adds no
second retry system.

## Explicit opt-out

When the master switch or the `product_watch` topic is off, Product Watch
evaluates nothing and writes nothing, and the day's slot is left to ordinary
reminders. When the customer turns either back on (`PATCH
/api/v2/today/notifications`, off→on only), every active watch is re-baselined,
so switching back on does not deliver a backlog of facts that became true while
they had asked not to hear. An unrelated preference change does not re-baseline.

## Notification topic

`product_watch` is a new typed topic in `NOTIFICATION_TOPICS`, never an alias of
an existing one; unknown topics remain fail-closed. It defaults on in the generic
topic map because a notice can only exist for a pack the customer explicitly chose
to watch — the watch is the opt-in. The master switch, native push consent and the
OS permission all remain separate and authoritative. The Notifications screen gains
a **Product watch** row.

## Delivery priority

`queue_for_product_watch` is the first trigger in the worker's existing ordered
list, ahead of environment crossings, protocol days, running-out, deferred
purchases and the agenda. It returns nothing unless a notice is actually waiting,
so ordinary reminders keep the slot on every other day. The daily cap is not
raised and there is no urgent bypass.

One cycle queues at most one Product Watch notice. With several waiting, exactly
one wins, deterministically:

1. class priority — record match, then regulatory change, then label change
   (delivery order, not severity);
2. stable observation order — the recall start date the register states, or the
   label version; never the time we noticed;
3. barcode, then the notice identity, as tie-breakers.

The rest stay eligible for later cycles.

## Deep link

`_target()` in `notifications.py` permits `/verdict` with exactly one parameter, a
`barcode` of 8 to 14 ASCII digits (`VERDICT_BARCODE`, `fullmatch`). Everything else
in the params is dropped; a missing or malformed barcode drops the destination
entirely. The app applies the same rule in `src/navigation/notifications.ts` and
falls back to `/scan`. No source URL, snapshot id, account id or query object ever
rides along.

## Concurrency

Lock order is the notification preference first, the watch rows second — in the
worker and in the preferences route — so the two cannot deadlock.

- **Watch disabled while a cycle is running.** The cycle holds the active watch
  rows `FOR UPDATE` from evaluation until the caller commits. A stop that arrives
  mid-evaluation waits; a stop committed first means the cycle does not see the
  watch. No notice is ever committed for a watch whose stop had already committed.
- **Re-anchor while a cycle is running.** The re-anchor locks the same row, so it
  waits, then rebuilds the baseline from the state as it now is — including a
  notice the cycle just decided.
- **Watch created while a cycle is running.** Not in the cycle's locked set; it
  starts from its own baseline and is evaluated next cycle.
- **Two scheduler calls overlapping / duplicate evaluation.** The preference lock
  serialises them; the second sees the advanced cursor and finds nothing. The
  outbox's unique dedup hash and claim lease remain the final authority.
- **Stale reads.** The worker reads the preference before it locks it, so after the
  lock is taken the row is re-read: an opt-out committed in between wins. Locked
  watch reads and anchor re-proofs bypass the session's identity map for the same
  reason.
- **Two creates at once.** The `(account_id, barcode)` unique constraint is the
  final authority; the insert runs in a savepoint and the loser reads the winner.

## API

All under the existing product surface, all requiring a signed-in account:

| Route | Device | Effect |
| --- | --- | --- |
| `GET /api/v2/scan/verdict/{barcode}/watch` | optional | Read state. Changes nothing, sends nothing |
| `PUT /api/v2/scan/verdict/{barcode}/watch` | required | Body `{"label_version": n}`. Watch or re-anchor. Idempotent |
| `DELETE /api/v2/scan/verdict/{barcode}/watch` | not needed | Stop. Idempotent; erases no delivery history |

Public state is `contract_version, barcode, watching, label_version, started_at,
watchable, anchorable_label_version, watching_this_pack, reason, delivery` — no
watch, snapshot, capture, product-record or account id, no fingerprint and no
cursor. Refusals: `barcode_not_watchable` (422), `confirmed_pack_required` (409),
`watch_context_changed` (409, the version the customer saw is no longer the pack's).

## Frontend

A small **VERIFIED CHANGES** card under the official record on the verdict screen,
shown only when signed in, not a reference view, the server granted
physical-pack context and a confirmed label version exists. It offers **Watch
verified changes**, then shows **Watching verified changes** (whether or not push
is on), **Watch this pack instead** when an earlier pack is watched, and **Stop
watching**. No bell, no red, no new tab. A failed request re-reads the server and
shows what it actually holds. Every value is stored with the barcode it was
fetched for, so one product's state is never rendered under another. All copy is
in `frontend/src/strings/productWatch.ts`.

## Privacy

`product_watches` is `INCLUDED` in the privacy registry and exported inside the
`product_scans` domain with customer-readable fields only: `barcode, watching,
label_version, started_at, stopped_at, last_notified_at, created_at, updated_at`.
The watch id, anchor ids and the cursor are not exported — they point at internal
rows the person cannot open, or record which official revisions were already known.
The export schema version is unchanged (1.4): the addition is one new list, and
previous additive lists did not bump it.

## Account deletion

`product_watches.account_id` is `ON DELETE CASCADE`, so the deletion job's final
step removes every watch with the account. A test runs the real deletion job and
checks the rows go, and another reads the constraint rules from `pg_constraint`.
`anchor_scan_event_id` is also `CASCADE` (no capture, no anchor);
`anchor_label_snapshot_id` is `RESTRICT`, because a label snapshot is shared
product truth and is never deleted underneath a watch.

## ODbL wall

The table holds a barcode, account-owned state, Store-B references and FSSAI Recall
Ids inside the cursor. It holds no Open Food Facts field — no product name, brand,
ingredients or nutrition — and there is no durable joined dataset. Matching uses
Store-B capture facts only; Open Food Facts can never supply a licence or lot, and
a test proves OFF brand and product names cannot make a record match.

## No FSSAI polling; existing scheduler reused

Product Watch consumes already-ingested official data; it does not acquire it.
There is no FSSAI scraping, CAPTCHA bypass, browser automation, private endpoint,
hidden API or new data source. Official data still enters only through the existing
manual governed import.

There is no new worker, scheduler, cron service, queue table, Redis or broker. The
existing notification cycle — invoked hourly by Supabase Cron through the existing
`/internal/scheduler/notifications` door, or by hand as
`python -m app.workers.notifications` — asks Product Watch for at most one notice.
Supabase Cron's frequency is unchanged. A Product Watch notice means *the next notification cycle
noticed a governed material fact in our stored authority* — not that GlamGenius
monitors FSSAI live. No paid infrastructure of any kind was added.

## Migration

One focused revision, `k9l0m1n2o3_step12c_product_watches.py` (`down_revision =
j8k9l0m1n2`), creating only `product_watches`: explicit `ondelete` on every foreign
key, `UNIQUE (account_id, barcode)`, `CHECK (anchor_label_version >= 1)`, `CHECK`
that `active` and `stopped_at` agree, and indexes on both anchors. Its downgrade
drops the table. Upgrade from empty PostgreSQL 16, `alembic check`, and the
downgrade/upgrade round-trip all run clean.

## Failure modes

| Failure | Result |
| --- | --- |
| Corrupt 12B ledger | 12B logs `regulatory_history_invariant_failed`; no notice |
| Corrupt 12A chain | `product_watch_label_history_invalid` logged; no notice |
| Malformed cursor | `product_watch_cursor_invalid` logged; watch skipped, not repaired |
| Anchor no longer provable | `product_watch_anchor_invalid reason=…` logged; watch goes quiet |
| Current pack with no resolvable version | `product_watch_snapshot_unresolved`; not watchable |
| Ambiguous match / omission / unopenable source | No notice |
| Provider failure | Recorded by the existing worker; not retried by Product Watch |

## Rollback

Revert the change and run `alembic downgrade j8k9l0m1n2`, which drops
`product_watches`. Nothing else in the schema changes. Outbox rows already
written for Product Watch notices remain ordinary delivery history (source kind
`product_watch`, destination `/verdict`); the app build that knows `/verdict`
routes them, and an older build falls back to `/scan`.

## Known limits

- The Product Constitution lists proactive alerts among paid features. There is no
  billing gate here because billing code is not permitted in this repository yet;
  gating Product Watch is a product decision for when that work is reviewed.
- While native push is off, the worker never visits the account, so nothing is
  decided; a notice that is still true when push is switched on may then be
  delivered once.
- A watch whose anchor capture is deleted disappears with it (`CASCADE`).
