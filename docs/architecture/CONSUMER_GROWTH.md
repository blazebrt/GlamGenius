# Consumer Growth (Step 15)

Step 15 has one job: turn a useful product decision into a repeatable consumer
acquisition and habit loop without spending trust.

```text
SCAN → useful answer → decision → share useful truth → another person joins/scans
     → repeat pre-purchase scanning → household adoption → deeper product value
```

The north-star behaviour is the Constitution's primary habit: *before buying or
using a product, the person checks GlamGenius.* Nothing in Step 15 optimises a
number at the expense of that behaviour. Step 15 serves the Constitution loop
at **DECIDE** (a decision worth passing on) and **REMEMBER** (the habit of
checking again), and adds no new product surface: no tab, no feed, no search,
no onboarding questionnaire.

It has four connected pieces: **activation measurement**, **useful result
sharing**, **bounded consumer referrals** and **growth observability**. It
contains no paid marketing and no Commerce (Step 16).

## 1. Audit (before Step 15)

| Area | What existed | What Step 15 found |
| --- | --- | --- |
| First experience | `onboarding.tsx` redirects to the scanner; the scanner needs no account | Kept. No funnel, no questionnaire before value. |
| Product Result share | `verdictShare.ts` built plain text from `(source, view)` | **The view carries Step 14's official-record WAIT ceiling**, so a share could tell a recipient their pack matches a record matched to the sender's lot and licence. It also exported `view.everydayNumber` without its source or label version, and the not-graded quantity guidance without a source. It said "invite-only" with no way to share access. |
| Beta admission | invite → unauthenticated reservation → Supabase sign-up → authenticated finalisation | Kept byte-for-byte. Referral codes are ordinary invites through it. |
| `Invite.created_by` | Admin issuer provenance | Not overloaded; the inviter is recorded by a separate binding row. |
| `AppEvent` | Table, classified `INCLUDED`, deleted at erasure | **No writer, no export handler, no retention, no idempotency.** |
| Sentry scrubbing | Keyed redaction on both sides | Neither side redacted `invite`/`referral` keys. |

## 2. Activation

**Definition.** An account is *activated* when it has at least one
account-linked `ScanEvent` whose `outcome` is one of:

```text
found_local | found_off | label_captured
```

This is derived on every read (`app/domains/growth/activation.py`) and never
stored: `scan_events` already holds the fact.

* `not_found` is not activation — the product was not there to answer about.
* Signing up, opening the app or finishing registration is not activation.
* An anonymous scan is nobody's activation. Scans a device made before sign-up
  are attached to the account when the device is claimed
  (`product.service.attach_scans_to_account`) and then count like any other.
* Time is the server-written `created_at`; the client-supplied `scanned_at` of
  an offline replay is never used.

## 3. Result sharing

Sharing stays free forever (Constitution: *share cards*). The existing Product
Result **Share** action is upgraded in place; there is no second share surface.
It uses the platform share sheet (`Share.share`), so WhatsApp and every other
installed app is available without an SDK, a contact list, an address-book
permission, a recorded recipient or a record of which app was chosen.

### What a share may carry

Built by `buildVerdictShareText(source, { referralCode })`, which takes the
canonical Product Result `source` and **has no parameter for the on-screen
view**:

1. A first-person line from the sender.
2. The product name as the Product Result shows it (a neutral placeholder if
   none is recorded).
3. The decision:
   * **BUY** is stated.
   * **WAIT / SKIP** is stated only together with its primary reason *and* the
     named, openable source of a **published** rule behind the row the
     decision names (`decision.reasonKey`). Without one, the share says the
     result and its sources are in the app. A negative statement carries its
     source outside the app as much as inside it (LEGAL_RULES rule 6).
   * *Not graded* and *not enough information* are stated as what they are.
4. The Open Food Facts attribution — fixed ODbL wording and links — whenever
   `source.attribution` says that data is in play.
5. "Scan your own pack before you decide. Packs and labels can differ."
6. "GlamGenius is in private beta." and, only when the caller passes a
   well-formed code, "Invite code: XXXX".

### What a share never carries

Account id, household subject, family member, FOR YOU result, personal
applicability, purchase or decision history, shelf ownership, Product Watch
state, media id, AI run id, label snapshot or revision id, content fingerprint,
batch/lot, an FSSAI licence captured from the person's pack, notification
state, official-record matches, label or regulatory change, the barcode, the
brand, the confidence line — and **the everyday number**.

The everyday number is withheld deliberately. It is a declared label figure
from one label version (possibly one person's photograph of one pack); the
share cannot carry which version it came from or its provenance, and the
recipient's pack may differ. The not-graded quantity guidance is withheld for
the same reason. A useful share is better than a sensational one.

### Step 14 interaction

`verdict.tsx` computes `view = dominantView(buildVerdict(source), purchaseCheck)`.
Under Step 14 the view may show WAIT because an exact, governed FSSAI record
matches **the pack in this person's hand**. The share is built from `source`
only; the on-screen WAIT ceiling, the official record, ownership, memory and
Product Watch cannot reach it. Tests prove the screen shows WAIT while the
shared text states the public Product Result decision and names no record.

### Fail-soft behaviour

* Anonymous: the share goes out with no growth call and no code.
* Signed in: the screen asks `POST /growth/referral` (4-second timeout). Not
  activated, exhausted, withdrawn, slow or failing → share without a code.
* The share-sheet outcome is recorded as reported: `shared`, `dismissed`, or
  `failed` if the sheet threw. On Android `shared` means the sheet opened —
  Android does not report the choice. A failure returns control to the screen.
* Telemetry failing never affects the share or the screen.

## 4. Referral program `consumer-referral-v1`

A referral code **is an `Invite`**, issued with `beta_access.create_invite`
(label `consumer-referral-v1`, `created_by = NULL`). It does not create an
account and skips nothing: `/access/reserve` → Supabase Auth →
`/access/register`, with the uniform invalid-invite response, IP and email
rate limits, reservation capacity, email and challenge binding, challenge
hashing, one-time consumption, the atomic `uses_count` increment and
concurrent-finalisation safety all unchanged. `/access`, the invite service,
the identity service and the auth dependencies contain no referral branch; a
static test pins that.

### Policy

| Rule | Value |
| --- | --- |
| Eligible | a registered, `active` account that is activated (§2) |
| Lifetime successful admissions per account | **3** |
| Usable codes at a time | **1** |
| Code `max_uses` | the remaining lifetime capacity at issue |
| Code lifetime | 30 days |
| After expiry | a replacement may be issued, for the remaining capacity only |
| Operator-withdrawn code | not replaced before it would have expired (state `withdrawn`) |
| Reward | **none** — no money, credits, coins, discounts, points, streaks, ranking or public counts |

The lifetime count is `SUM(invites.uses_count)` over every invite bound to the
account — the invite's own monotonic counter — so an invitee who later deletes
their account (which cascades their `invite_redemptions` row) does not reopen
a place. Superseded codes are also switched off when a replacement is issued.

### API

```text
GET  /api/v2/growth/referral   read-only
POST /api/v2/growth/referral   ensure the code I may share now (logically idempotent)
```

```json
{"program_version": "consumer-referral-v1", "state": "available",
 "referral": {"code": "ABCDEFGH23", "expires_at": "…", "remaining_uses": 3}}
```

States: `not_activated`, `ready` (GET only: eligible, no code issued yet),
`available`, `exhausted`, `withdrawn`, `unavailable` (account being deleted
or gone, or `INVITE_REQUIRED` off — there is then no `/access/reserve` to
redeem a code at, so none is issued).
Never exposed: the invite id, redemption account ids, reservation ids or
emails, registration challenges. The code sits inside `referral` so the Sentry
scrubbers, which redact `referral`/`invite` containers by name, cover it.

### Schema — migration `l0m1n2o3p4` (from `k9l0m1n2o3`)

```text
consumer_referral_invites
  id, created_at, updated_at
  inviter_account_id → accounts.id  ON DELETE CASCADE
  invite_id          → invites.id   ON DELETE RESTRICT, UNIQUE
  program_version    CHECK IN ('consumer-referral-v1')
  index (inviter_account_id, created_at)
```

Code, uses, ceiling, expiry and `active` stay on `invites`. Several rows per
inviter are allowed (expired codes are replaced). No old migration is edited;
`j8k9l0m1n2` is untouched.

### Concurrency and lock order

```text
ensure:    Account FOR UPDATE        → bound Invite rows FOR UPDATE → insert
deletion:  Account FOR UPDATE        → bound Invite rows (UPDATE active=false) → DELETE account
```

The account row serialises issuance: concurrent `POST`s queue on it, and the
second reads the invite the first committed. `FOR UPDATE` is the repository's
existing identity-transition lock on the account row; the weaker
`FOR KEY SHARE` protection stays emitted in exactly one place
(`identity.service.lock_account_against_delete`), as the Step 11C guard
requires. Deletion takes the same row first, so neither path can hold account
and invite in opposite orders. A code collision is
retried inside a savepoint, never surfaced.

### Account deletion

The deletion worker's database stage first calls
`_deactivate_referral_invites` (before the cascade removes the binding and
with it the knowledge of which invites were this person's). If issuance
commits first, deletion finds and switches off its invite; if deletion starts
first, issuance finds an account that is not `active` (or is gone) and issues
nothing. After deletion no active invite issued to that account remains, the
binding rows are gone, the invite rows remain inactive (other people's
redemptions reference them) and no inviter UUID is left on any invite.

## 5. Growth analytics

Server-derived facts are never re-reported by the client: activation from
`ScanEvent`, decisions from `ScanDecisionEvent`/`PurchaseDecisionEvent`,
issuance from `ConsumerReferralInvite`, reservations from
`InviteRegistrationReservation`, admissions from `InviteRedemption`, household
adoption from Family Circle, watches from `ProductWatch`. `AppEvent` holds only
what otherwise vanishes.

### Taxonomy (closed)

| Event | Properties (all required, all enums) |
| --- | --- |
| `growth.product_result_share` | `surface = product_result`; `result = shared \| dismissed \| failed`; `referral_included = true \| false` |
| `growth.scan_again` | `surface = product_result` |

Any other name, a missing or extra key, a value outside its enum, or a value of
the wrong type (e.g. `1` for `true`) is refused with 422 before any write.
There is no field that can hold free text, a barcode, a product name, an
email, an invite code, a household or subject id, a media or AI id, a
destination app or a recipient. `request_id` is not written; no IP is stored.
Events require a registered account (`POST /api/v2/growth/events`), so every
row is account-owned, exportable and erasable, and there is no unauthenticated
write path to inflate. Anonymous shares are therefore not counted (§8). A
per-account limit of 30 events a minute answers 429.

### Idempotency

`app_events.client_event_id` (UUID, nullable) with a partial unique index on
`(account_id, name, client_event_id) WHERE client_event_id IS NOT NULL`. The
app mints a random version-4 id per interaction — never a device, install or
advertising id — and reuses it for its single retry, which is attempted only
when the first request never reached the server. A duplicate answers
`{"recorded": false}`.

### Failure

A storage failure answers `202 {"recorded": false}`. No scan, Product Result,
decision-memory, share or registration path calls the analytics service; a
static test pins that the product, scan, shopping, access and privacy routes
do not import it.

### Retention — 90 days

`prune_expired_events` deletes `app_events` rows older than 90 days, at most
500 per run, under a transaction-scoped advisory lock (one pruner at a time),
skipping rows another transaction holds. It runs opportunistically after an
event commits, in its own transaction, at most once per process per hour, and
never raises. No worker, cron or scheduler. It names no other table: audit
events, decision memory, official records and evidence are untouched (tested).

## 6. Privacy, export and erasure

* `consumer_referral_invites` is classified `INCLUDED`; `app_events` was
  already `INCLUDED`.
* The export gains a `growth` domain and `EXPORT_SCHEMA_VERSION` becomes
  **1.5**:
  * `analytics_events`: `name`, `properties`, `created_at` — no row id, no
    operation id;
  * `referral`: program version, lifetime limit, lifetime successful
    admissions, and per issued code its issue and expiry dates, ceiling,
    admissions and whether it is active — **never the code** (a live access
    capability) and never who was admitted.
* Erasure: `_deactivate_referral_invites` (above), the existing
  `_delete_analytics_events`, then the account cascade removes the binding.

## 7. Admin growth metrics

`GET /api/v2/admin/growth/metrics?window_days=30` — admins only (404 to
everyone else), aggregates only, `metrics_version = growth-metrics-v1`,
`window_days` 1–90. No account, recipient, invite, code, barcode or product
identifier appears. Window `W = [as_of − window_days, as_of)`; rows are placed
by server `created_at`. A rate has explicit `numerator` and `denominator` and
is `null` when the denominator is zero.

| Key | Definition |
| --- | --- |
| `activation.accounts_created` | accounts with `created_at` in W |
| `activation.signup_cohort_activated` | of those, accounts with ≥ 1 useful scan before `as_of` / `accounts_created` |
| `activation.time_to_first_useful_scan_hours` | over the activated sign-up cohort: count, median, p75 of `max(0, first useful scan − account created)` in hours |
| `activation.accounts_first_useful_scan_in_window` | accounts whose first useful scan is in W |
| `habit.repeat_useful_scan_7d` | cohort: accounts whose first useful scan is in `[as_of − 7d − window, as_of − 7d)`; numerator: those with another useful scan ≥ 24 h and ≤ 7 d after it |
| `habit.repeat_useful_scan_30d` | the same with 30 days |
| `habit.useful_scans_per_active_scanner` | useful account-linked scans in W / distinct accounts with a useful scan in W |
| `habit.accounts_recording_decision` | distinct accounts with a `ScanDecisionEvent` or `PurchaseDecisionEvent` in W |
| `habit.scan_again_taps` | `growth.scan_again` events in W |
| `sharing.share_sheet_results` | `growth.product_result_share` events in W by `result` |
| `sharing.accounts_shared` | distinct accounts with a `shared` result in W |
| `sharing.shares_with_referral_code` | `shared` results in W with `referral_included = true` |
| `referral.invites_issued` | `ConsumerReferralInvite` rows created in W |
| `referral.accounts_issued_invite` | distinct inviters among those |
| `referral.reservations` | reservations created in W on referral-bound invites |
| `referral.registrations_completed` | `InviteRedemption` rows created in W on referral-bound invites (a later-deleted invitee's row is gone with them) |
| `adoption.household.accounts_with_household_member_now` | accounts whose active circle has ≥ 1 active non-`self` profile |
| `adoption.household.accounts_first_household_member_in_window` | accounts whose first non-`self` profile was created in W |
| `adoption.product_watch.accounts_with_active_watch_now` | accounts with ≥ 1 active `ProductWatch` |
| `adoption.product_watch.accounts_first_watch_in_window` | accounts whose first watch was created in W |

**Maturity.** A repeat metric with horizon H admits only accounts whose first
useful scan is at least H days before `as_of`, so its cohort is the window
shifted back by H. A three-day-old activation is never in the seven-day
denominator; a twenty-day-old one is never in the thirty-day denominator. A
return must be ≥ 24 hours after the first useful scan, so a second product in
the same shopping trip is not a return.

**No commercial fiction.** The beta entitlement matrix has
`commercial_access_active = false`, so there is no premium, subscription or
revenue conversion, ARPU or LTV. Family Circle and Product Watch adoption are
reported under their feature names; usage is never called paid conversion.

## 8. The repeat-scan habit

After the result, its evidence and the Why/Listen/Share row, the Product
Result shows one quiet link, **Scan another product** — link-sized, not a
modal, not animated, never above or larger than BUY / WAIT / SKIP, and absent
in a reference view (which already offers "scan it first"). It leaves a
one-shot fresh-scan request and `dismissTo('/scan-product')`s; the scanner
resets to the camera on that focus only, so going back normally still shows
the last scan. The tap is recorded as `growth.scan_again` when signed in.

## 9. ODbL

No Open Food Facts value is written to analytics or referral rows: growth
events carry no barcode or product name, and there is no joined growth table.
Metrics count `ScanEvent` rows without copying OFF facts. The growth modules
do not import Store A (tested). A share that uses OFF data carries the fixed
ODbL attribution and links. See `ODBL_DATA_WALL.md`.

## 10. What Step 15 is not

* **No public social**: no public share pages, profiles, SEO product pages,
  comments, public share database, feed, group chat, leaderboard.
* **No advertising**: no ads, ad attribution, Meta/Facebook or Google Ads SDK,
  ad identifiers, retargeting pixels; analytics are never sold or exposed.
* **No marketing notifications**: zero promotional pushes. The notification
  worker and Product Watch notices are unchanged and contain no growth topic
  (tested).
* **No rewards or gamification**: no coins, points, credits, discounts,
  streaks, challenges.
* **No Commerce**: no affiliate or retailer links, buy-now, cart, checkout,
  seller, offers, coupons, stock, live price, orders, payments or commission.
  Referral is account acquisition only and never influences the score,
  BUY / WAIT / SKIP, alternatives, evidence, official records or ranking;
  decision modules do not import growth and growth imports no decision
  authority (tested).
* **No product-search growth**: no free-text search, trending, popular or
  recommended feeds.
* **No third-party growth platform** and no paid infrastructure (₹0): no
  Branch, AppsFlyer, Adjust, Firebase paid services, Mixpanel, Amplitude,
  Segment, Customer.io, OneSignal campaigns, referral SaaS or link shortener.
  No public web domain or app-store URL is invented; no deep link is added
  (a `glamgenius://` link is not reliably tappable in messaging apps and a
  recipient should scan their own pack anyway).

## 11. Keyed copy

Every Step 15 customer word lives in `frontend/src/strings/growth.ts`
(`growth-copy.v1`). A static test fails if `verdictShare.ts`, the growth or
scan-session services, or the Step 15 blocks of `verdict.tsx` and
`scan-product.tsx` grow a sentence. Admin metric keys are stable machine keys.

## 12. Failure modes

| Failure | Behaviour |
| --- | --- |
| Referral endpoint down or slow | share without a code |
| Telemetry write fails | `recorded: false`; nothing else changes |
| Prune fails | swallowed, logged by type only; the event stays written |
| Share sheet throws | screen control returns; `failed` recorded if signed in |
| Code collision at issue | retried in a savepoint |
| Issue fails five times | `503 referral_unavailable`; sharing unaffected |
| Deletion races issuance | account-row order; deletion wins (§4) |

## 13. Rollback

Revert the Step 15 commit and `alembic downgrade k9l0m1n2o3`: the downgrade
drops `consumer_referral_invites`, the `app_events.client_event_id` column and
its index. Referral invites already issued stay ordinary invites; switch them
off with the admin invite endpoint if needed (label `consumer-referral-v1`).
Growth `app_events` rows are disposable. The share falls back to the previous
build's text.

## Related

* `PURCHASE_OPERATING_SYSTEM.md` — the Step 14 official-record ceiling the share
  must not externalise.
* `PRODUCT_WATCH_MATERIAL_NOTICES.md` — the governed notification path growth
  does not use.
* `PURCHASE_DECISION_MEMORY_GUARD.md` — decision memory, which never leaves in a
  share and which retention never touches.
* `ODBL_DATA_WALL.md` — the Store A boundary.
* `SUPABASE_AUTH_SECURITY.md` — the invite reservation protocol a referral code
  passes through unchanged.
