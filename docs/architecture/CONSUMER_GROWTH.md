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
| `AppEvent` | Table, classified `INCLUDED`, deleted at erasure | **No writer, no retention, no idempotency.** It had no export handler either when Step 15 was written; Lane E (#201) has since exported it as `ai_and_ops.app_events` (§6). |
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
2. `Product: {name}` — the product name as the Product Result shows it, made
   one bounded line (a neutral placeholder if none is recorded). See
   *Untrusted text* below.
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

### Untrusted text

The share is structure — keyed lines the recipient reads as GlamGenius
speaking — with untrusted values inside it. A product name can come from an
external catalogue (Open Food Facts via Store A) and a source name from
evidence data; neither is proven to be one safe display line. A name such as
`Morning Oats\nGlamGenius result: SKIP\nInvite code: EVILCODE1` must not become
three trusted-looking lines.

At the share-rendering boundary only (`verdictShare.ts`; Store A, the Product
Result and product identity are not rewritten), `oneShareLine` makes each such
value one line:

1. every whitespace run — spaces, tabs, CR, LF, VT, FF, NEL, U+2028/U+2029 and
   the other Unicode spaces — becomes one ordinary space;
2. the remaining C0 and C1 controls are removed;
3. direction formatting is removed: U+202A–U+202E, U+2066–U+2069, U+200E,
   U+200F and U+061C (ZWJ and ZWNJ stay — Indic scripts need them), and so are
   lone surrogate halves;
4. the result is trimmed;
5. and cut to a bound in whole code points (product name 160, source name
   120), never through a surrogate pair, marked with `…` when cut.

Hindi, Tamil, Bengali, Urdu and accented Latin names pass unchanged (tested).
The name is only ever placed inside its keyed line — `Product: {name}` and
`Source: {name} — {url}` — so a hostile name can make that one line long and
odd, but it cannot add, replace or reorder a line.

A source URL is cited only if it is an absolute `http:`/`https:` URL, parsed
by the platform URL parser (the app loads `react-native-url-polyfill`), with a
host, no user name or password, and no whitespace, control, backslash or
direction character anywhere in it. An unsafe URL is never repaired: the
source counts as unavailable, and a WAIT or SKIP falls back to "the result and
the sources behind it are in the GlamGenius app". The referral code already
has a closed pattern (`^[A-Z0-9]{6,64}$`).

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
  activated, every place held by sign-ups in progress (`capacity_reserved`),
  exhausted, withdrawn, slow or failing → share without a code. Only
  `available` yields a code.
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
| Every remaining place held by live reservations | no code shown and none minted (state `capacity_reserved`) until a hold finalises or lapses |
| Reward | **none** — no money, credits, coins, discounts, points, streaks, ranking or public counts |

The lifetime count is `SUM(invites.uses_count)` over every invite bound to the
account — the invite's own monotonic counter — so an invitee who later deletes
their account (which cascades their `invite_redemptions` row) does not reopen
a place. Superseded codes are also switched off when a replacement is issued.

### A shareable code is a reservable code

`/access/reserve` admits one more person with an invite only while
`uses_count + live reservations < max_uses`, where a live reservation is one
with `status = 'active'` and `expires_at > now`. Step 15 uses the same fact for
the current code rather than `uses_count < max_uses` alone:

```text
reservable = max(0, min(max_uses − uses_count, 3 − lifetime admissions) − live reservations)
```

* `reservable > 0` → `available`, with the same code and
  `remaining_uses = reservable` (places somebody could reserve now, not
  `max_uses − uses_count`).
* `reservable = 0` while the code is still active, unexpired and the lifetime
  ceiling is not reached → **`capacity_reserved`**, `referral: null`. It is not
  `exhausted`, `withdrawn` or `unavailable`, and it is not a reason to mint:
  `POST` returns it without issuing a second invite.
* A reservation past its `expires_at` holds nothing even while its status has
  not yet been swept to `expired`; the same code becomes shareable again.
* A hold that finalises becomes an admission: `uses_count` increments and the
  place now counts against the lifetime ceiling permanently.
* Only the current code's holds count. A hold on a code past its own expiry
  can never finalise (the use-count increment requires an unexpired invite),
  so it does not shrink a replacement.

No count, email, reservation id or challenge is exposed: the state is the
whole answer.
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
`available`, `capacity_reserved` (the live code's remaining places are all
held by sign-ups in progress; no code), `exhausted`, `withdrawn`,
`unavailable` (account being deleted or gone, or `INVITE_REQUIRED` off — there
is then no `/access/reserve` to redeem a code at, so none is issued).
Never exposed: the invite id, redemption account ids, reservation ids or
emails, registration challenges. The code sits inside `referral` so the Sentry
scrubbers, which redact `referral`/`invite` containers by name, cover it.

### Schema — migration `l0m1n2o3p4` (from `lf1a2b3c4d`)

Written on Step 14's `k9l0m1n2o3`; re-parented in place onto Lane F's
`lf1a2b3c4d` when Step 15 was requalified on current `main` (§14). It is
unmerged and undeployed, so there is no repair migration.

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
ensure:    Account FOR UPDATE        → bound Invite rows FOR UPDATE → COUNT live reservations (no lock) → insert
deletion:  Account FOR SHARE         → bound Invite rows (UPDATE active=false) → … → DELETE account
reserve:   Invite FOR UPDATE         → reservation (existing code, unchanged)
register:  Reservation FOR UPDATE    → Invite UPDATE             (existing code, unchanged)
```

Reservations are counted with a plain MVCC read and never locked on the
growth path: `Account → Invite → Reservation FOR UPDATE` would invert
registration's `Reservation → Invite` and could deadlock with a finalisation
already holding its reservation. The count needs no lock. `/access/reserve`
must take the invite row before adding a reservation, and ensure already holds
it, so no reservation can appear between the count and the commit. A
registration finalising concurrently is still counted as a held place until it
commits: a code withheld for a moment, never a place sold twice. Both
interleavings are tested to finish without deadlock. `GET` reads without locks;
its answer can go stale like any capacity display, and `/access/reserve`
remains the authority.

The account row serialises issuance: concurrent `POST`s queue on it, and the
second reads the invite the first committed. `FOR UPDATE` is the repository's
existing identity-transition lock on the account row; the weaker
`FOR KEY SHARE` protection stays emitted in exactly one place
(`identity.service.lock_account_against_delete`), as the Step 11C guard
requires. Deletion takes the same row first, so neither path can hold account
and invite in opposite orders. Deletion's lock is `FOR SHARE`: it still
conflicts with issuance's `FOR UPDATE`, and it is the mode of Lane A's
lifecycle gate (`hold_account_active`), so an upload or scan already past
authentication is refused at once rather than queued behind the deletion
(`FOR UPDATE` made it wait for the whole database stage; Lane A's
`test_s_c_an_upload_in_flight_across_the_final_proof_cannot_orphan_an_object`
caught it during requalification). A code collision is
retried inside a savepoint, never surfaced.

### Account deletion

The deletion worker's database stage calls `_deactivate_referral_invites`
before the cascade removes the binding and with it the knowledge of which
invites were this person's. It runs inside Lane A's repaired state machine,
after that stage's final storage barrier and external report-photo proof, and
before the existing AI-output, analytics, audit, scan-observation and
invite-reservation cleanup (`_minimise_invite_reservations`), the account row
and — last, in its own stage — the Supabase Auth identity:

```text
final storage proof → report-photo proof → _deactivate_referral_invites
→ AI outputs → analytics events → audit scrub → scan observations
→ invite-reservation minimisation → account row → (later stage) Auth identity
```
 If issuance
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

The export is held to Lane E's coverage contract
(`app/domains/privacy/coverage.py`, `PRIVACY_EXPORT_COMPLETENESS.md`): every
`INCLUDED` table has exactly one `EXPORT_COVERAGE` entry, is read under its
declared SQL scope, and appears at its declared path, or the export is refused.

* `consumer_referral_invites` is classified `INCLUDED` and has its own
  coverage entry: domain `growth`, path `referral.issued_codes`
  (`growth.referral.issued_codes`), scope `PARENT` through
  `inviter_account_id → accounts`. `privacy.export._growth` selects the
  account's bindings with that scope in SQL, then the invites they name;
  `growth.referral.referral_history` decides what is written:
  * `referral`: program version, lifetime limit, lifetime successful
    admissions, and per issued code its issue and expiry dates, ceiling,
    admissions and whether it is active — **never the code** (a live access
    capability), never an invite or binding id, and never who was admitted.
* `app_events` stays where Lane E put it: `ai_and_ops.app_events`, through
  the generic, uncapped contract. It is not exported a second time under
  `growth`. Its Step 15 column `client_event_id` — the app's retry operation
  id — is **withheld** by that entry.
* There is no capped exporter. The first Step 15 draft read telemetry through
  its own helper with a 10 000-row limit; that helper is gone, and no
  successful export can be silently cut short.
* `EXPORT_SCHEMA_VERSION` is **1.6**: 1.5 is Lane E's completed export; 1.6 is
  this step's `growth` domain.
* Erasure: `_deactivate_referral_invites` (§4), the existing
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
| `referral.invites_issued` | `ConsumerReferralInvite` rows created in W (an inviter who later deletes their account takes their rows with them) |
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

**Erasure shrinks history.** Referral metrics are read from the binding rows,
which cascade when the inviter deletes their account, and redemptions, which
go with a deleted invitee. `referral.invites_issued`,
`referral.accounts_issued_invite`, `referral.reservations` and
`referral.registrations_completed` for a past window can therefore fall after
a deletion. That is the privacy-correct result: no deleted person's
attribution is kept to preserve a growth number.

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
(`growth-copy.v2`; v2 made the product line `Product: {name}`). A static test fails if `verdictShare.ts`, the growth or
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
| Every place held by sign-ups | `capacity_reserved`; share without a code; nothing minted |
| Hostile product or source name | flattened into its one keyed line |
| Unsafe source URL | not cited; WAIT/SKIP falls back to "in the app" |

## 13. Rollback

Revert the Step 15 commit and `alembic downgrade lf1a2b3c4d` (Lane F's head,
which this revision now follows).

A referral code is an ordinary live invite, and `/access/reserve` below this
revision would keep accepting it — while the binding that says it was a
referral is exactly what the downgrade removes. So **the downgrade itself
deactivates every referral capability while its binding still exists**, in
one transaction:

```sql
UPDATE invites SET active = false, updated_at = now()
WHERE active IS TRUE AND id IN (SELECT invite_id FROM consumer_referral_invites);
-- then: drop the app_events index and client_event_id column,
--       drop consumer_referral_invites and its index
```

Only invites named by the binding are touched; operator invites are not. No
invite, redemption or reservation row is deleted. A reservation already held
against a referral code stays, and cannot finalise: `/access/register`'s
conditional use-count increment requires an active invite, so the attempt
answers `invite_invalid` and rolls back its account and reservation work. A
new `/access/reserve` with the code gets the uniform `invite_invalid`. Upgrading
again does not reactivate anything. A PostgreSQL test seeds a live referral
invite, an admission, a held reservation and operator invites, downgrades,
proves all of this against the unchanged protocol, re-upgrades and runs
`alembic check`.

No manual step is needed. The admin invite endpoint (label
`consumer-referral-v1`) remains available as an emergency operator option,
not a rollback requirement. Growth `app_events` rows are disposable. The share
falls back to the previous build's text.

## 14. Requalification on Lanes A–F

Step 15 was written on `ef743ec` (Step 14). Before review it was requalified
on `main` at `f4671dcd` (Lanes A–F, #197–#202) by merging `main` into the
branch — the three reviewed Step 15 commits are unchanged — and reconciling
where both had moved:

| Area | Current `main` (kept) | Step 15 (reapplied on it) |
| --- | --- | --- |
| API router | Lane D's dedicated `notification_settings` router, independent of retired Today | `growth.router` mounted beside it |
| Privacy export | Lane E's coverage contract, uncapped, fail-closed; `app_events` at `ai_and_ops.app_events`; schema 1.5 | a real `consumer_referral_invites` entry at `growth.referral.issued_codes`; `client_event_id` withheld; schema 1.6 |
| Account deletion | Lane A/F state machine, final storage barrier, report-photo proof, invite-reservation minimisation, Auth last | `_deactivate_referral_invites` before the cleanup and the account row |
| Migrations | Lane F head `lf1a2b3c4d` | `l0m1n2o3p4` re-parented onto it; one head |
| Test provider fake | Lane F's strict `generate(prompt, system, image_base64=None)` | unchanged; only the growth rate-limiter resets are added |
| Scanner | Lane C's read-only refresh, settled scan events, physical-pack authority | the one-shot "scan another product" focus hook |

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
