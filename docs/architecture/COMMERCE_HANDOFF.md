# Commerce Handoff (Step 16 — Trust-Preserving Commerce V1)

> AI reads. Structured intelligence knows. Deterministic rules decide. AI explains.
> No source → no claim.

Commerce comes **after** BUY / WAIT / SKIP. It is never an input to one.

```text
Product Truth ─► Purchase OS (step-14-v1) ─► commerce-handoff-v1 ─► one disclosed outbound search link
                                     ▲                                  │
                                     └──────── nothing flows back ◄─────┘
```

## 1. Mission

When the engine has already decided, and only then, give the person one quiet,
disclosed way to look for the product the decision supports:

- a **BUY** → "Find this product", for the pack in their hand;
- a **WAIT** or **SKIP** → "Find this alternative", for the one comparable
  alternative the Product Result already shows, and only when that
  alternative's own decision is BUY.

The link opens a partner's **search page for one exact barcode**. GlamGenius
does not fetch that page, does not check what it lists, does not see what the
person does there, and says so.

Commerce V1 is **off by default**. Nothing appears anywhere until an operator
names one partner (§7).

## 2. Audit (before Step 16)

| Area | What existed | What Step 16 did |
| --- | --- | --- |
| Decision | Product Result (`read_product_verdict`), Purchase OS `step-14-v1` with the official-record WAIT ceiling | **Reused as-is.** Commerce reads the finished Purchase OS answer; it changes nothing in either. |
| Device authority | `X-Device-Token` via `current_device`; physical-pack context proven server-side | **Reused.** The handoff route takes the same dependency; there is no weaker path. |
| Alternative | Step 6A: at most one candidate, own canonical decision, no ranking | **Reused.** Commerce links that one candidate or nothing. It never finds, ranks or replaces one. |
| Telemetry | `app_events` with Step 15's closed writer, 90-day retention, idempotency index, export under `ai_and_ops.app_events` | **Reused.** A second closed writer for exactly one event; no new table. |
| External links | `externalLinks.ts` (http/https allowlist, `Linking.openURL`) | **Reused**, behind a stricter per-partner check. |
| Rate limiting | `FixedWindowLimiter` | **Reused** for the events route. |

**Collisions with Steps 14 and 15**, each resolved without weakening anything:

- `PURCHASE_OPERATING_SYSTEM.md` §13 says the Purchase OS has no affiliate
  link. That is still true: the Purchase OS module, its routes and its
  fingerprint carry nothing of Commerce (tests E). §13 now points here.
- The `AppEvent` docstring said Step 15 was the only writer. It now names both
  closed writers.
- `BetterOption.tsx` says the card has no affiliate link. The card still never
  builds or chooses one; Step 16 passes a rendered, already-validated link into
  an optional slot. The docstring says so.
- CI forbids `stripe`, `payment_intent`, `billing` and `subscription` anywhere
  in `backend/app`, and the legacy-terms test forbids `checkout` and
  `paywall`. Commerce code uses none of them (the metrics' "not reported" list
  says `order_completion`).

**Not built** (and never in V1): anything in §15.

## 3. Independence

The dependency points one way: Product Truth / Purchase OS → Commerce.

- No module under `nutrition`, `evidence`, `official_records`, `alternatives`,
  `purchase`, `product`, `off`, `value`, `recommendation`, `routines`,
  `supplements`, `care`, `inventory`, `growth`, `planning` or `family` imports
  Commerce (test E). The only files that name it are its own package, its route
  module, the router that mounts it, and the start-up configuration check.
- `commerce/handoff.py` imports three **constants** from outside Commerce: the
  Purchase OS contract version, the `decided` state and the alternative's
  `available` status. It reads no decision internals, no evidence, no grading,
  no AI, no Store A.
- The Purchase OS decision fingerprint contains no partner, affiliate,
  commission, merchant, price or URL field.
- **Byte-for-byte proof**: with Commerce off, with the partner on and no tag,
  and with two different affiliate tags, the Product Result, the anonymous
  purchase check and the signed-in purchase check return identical bytes
  (test E).
- The route asks the Purchase OS for the canonical answer with **no principal
  and no subject**, so Decision Memory, the shelf and any override are never
  read (test D).

## 4. The eligibility matrix (`commerce-handoff-v1`)

`app/domains/commerce/handoff.py` is the one authority. A target exists only
when **every** precondition holds:

1. the answer is `step-14-v1`, context `scan` / `scan_product`, no boundary;
2. not a reference view; identity `exact`; `physical_pack_context` proven
   true — the pack is in this device's hands, so the official-record ceiling
   has had its chance to govern the decision;
3. decision state `decided`.

| Verdict | Target | Additional conditions |
| --- | --- | --- |
| `buy` | current product | its barcode is an exact GTIN |
| `wait` / `skip` | the canonical alternative | alternative `available`; its **own** decision is `buy`; its barcode is an exact GTIN and is not the scanned one |
| anything else | none | — |

Everything else gets no target and a stable reason code:
`partner_not_configured`, `unsupported_context` (including the supplement
purchase prohibition and every non-scan strategy), `reference_view`,
`identity_insufficient`, `physical_pack_required`, `decision_not_made`
(`not_enough_information`, `prohibited`, `unsupported`), `barcode_unusable`,
`no_eligible_alternative`, `unsafe_destination`.

**Exact barcode** means a GS1 GTIN-8, -12, -13 or -14 of ASCII digits with a
valid check digit, not all zeros.

**Deliberate tightening.** Step 6A may offer, against a SKIP, a candidate whose
own decision is WAIT. Commerce links only a candidate the engine itself would
BUY: it never earns from a product the engine would tell somebody to wait on.
This removes links; it never picks a different candidate.

## 5. The official-record ceiling

When an exact, current official record governs the pack in hand, the Purchase
OS turns `buy` into `wait`. Commerce sees `wait`, so:

- the **current product is never linked** (tests C, on the real importer and
  matcher);
- an eligible alternative still may be, because the ceiling is about this pack,
  not the other product.

The app enforces the same rule a second time: a handoff is dropped unless its
decision equals the canonical decision the screen already shows.

## 6. What the person chose is not an input

A recorded BUY after a SKIP, a historical BUY on an earlier label version,
ownership on the shelf — none of them unlocks a link (tests D). The handoff
route never passes an account or subject to the Purchase OS, and
`build_handoff` ignores `memory` and `ownership` even when present.

## 7. The partner registry

`app/domains/commerce/partners.py` is a **closed literal table**, frozen with
`MappingProxyType`:

| Field | `amazon_in` |
| --- | --- |
| `key` | `amazon_in` |
| `display_name` | `Amazon.in` |
| `host` (exact) | `www.amazon.in` |
| `search_path` | `/s` |
| `query_parameter` | `k` (the barcode) |
| `affiliate_parameter` | `tag` (optional) |

There is no ranking, score, priority, weight, commission, payout or conversion
field (test G). There is no database table, no remote configuration and no
client-supplied partner.

**Configuration** (both empty by default, see `env.example`):

```text
COMMERCE_PARTNER=amazon_in          # exactly one registry key, or empty
COMMERCE_AFFILIATE_TAG=<your-tag>   # optional; identifies GlamGenius, never a person
```

A malformed value (unknown partner, tag without partner, malformed tag) is
refused at start-up **in every environment**, rather than silently turning the
handoff off where nobody would notice.

**Before enabling — operator checklist.** The partner's own programme pages
could not be read from the build environment, so none of these has been
verified by this change:

1. The partner programme's current operating agreement permits links from a
   mobile app, and to a search results page.
2. The programme's required disclosure wording is met by the keyed disclosure
   (§9), or the copy is updated through `commerce-copy.v1` → `v2`.
3. Searching the partner for a real barcode returns that product.
4. The affiliate tag is GlamGenius's own account tag.

## 8. The pack and the listing are different things

A listing may be another batch, lot, label version, formula or manufacturing
date. Nothing pack-specific transfers to it. Every `available` answer carries
`pack_notice: rescan_received_pack`, and the app shows **"Re-scan the pack you
receive before you use it."** The copy never says the product was found, is
the same, is in stock or available, is the best or cheapest, or comes from a
recommended seller (frontend guards).

## 9. Disclosure

Upfront, in the same block as the action, above it, and never hidden after a
tap:

> **Affiliate · GlamGenius may earn a commission. This does not affect our decisions.**

All Step 16 text lives in `frontend/src/strings/commerce.ts`
(`commerce-copy.v1`). Static guards fail if a Step 16 file grows its own
sentence, if the disclosure changes, or if the copy says anything about stock,
price, deals, sellers, urgency, baskets or growth.

**Placement.** BUY: "Find this product" after the decision, the purchase
context, the evidence and the community observations, before the alternative
card, in the same quiet link style as the screen's other lower links. WAIT /
SKIP: "Find this alternative" inside the existing alternative card, beneath
its MRP comparison. No second card, no reordering (a test proves every other
line renders identically, in the same order, with or without the link). No
empty card, no disabled button, no pressure copy.

## 10. Address security

The backend builds the address and then checks it **byte for byte** against
the one address the registry would build:

- `https://` exactly (the raw prefix too, since URL parsing lower-cases the
  scheme); the exact host, so no user-info, port, suffix or subdomain trick;
  the exact path; no fragment;
- exactly `k=<barcode>` and optionally `&tag=<tag>`, in that order, encoded
  exactly as the registry encodes them: no extra, repeated, reordered,
  alternatively encoded or double-encoded parameter;
- every character printable ASCII, no backslash, 256 characters at most.

Anything else is `unavailable` / `unsafe_destination`. The app checks the same
shape again with its own per-partner pattern before showing the link, and once
more before opening it. It opens through the existing http/https allowlist and
`Linking.openURL`, with no merchant SDK, in-app browser or WebView.

There is no SSRF surface: the backend never fetches the partner. There is no
open redirect: no route accepts a URL, host, partner or tag from a client.
**No log line contains the address or the tag** (tests B).

## 11. Analytics

One closed event over the existing `app_events` table:

```text
commerce.outbound_open
  surface   = product_result
  target    = current_product | alternative
  decision  = buy | wait | skip        (consistent with target)
  partner   = a registry key
  affiliate = true | false
```

No other key, no free text — so no barcode, product, brand, address, tag,
search, account, device, household or subject id, order, amount or commission
can be stored. `client_event_id` (a random operation UUID) makes retries
idempotent. **Account-only**: an anonymous device still gets its link, and its
open is not recorded. Recorded only after the link actually opened.

`POST /api/v2/commerce/events` is separate from growth telemetry (growth
refuses this name, this route refuses growth's), authenticated, rate-limited
per account (30 a minute) and fail-soft (a write failure returns
`{"recorded": false}`).

`GET /api/v2/admin/commerce/metrics` (admins only; 404 to anyone else) reports
aggregates over a 1–90 day window: outbound opens, accounts opening, opens by
target, partner and decision, each with a written definition. It explicitly
does **not** report purchases, conversion rate, revenue, GMV, commission,
average order value, return on ad spend or order completion: GlamGenius does
not have those numbers, and reporting them would be inventing them.

## 12. Privacy

- No new table, column or migration. Alembic head stays `l0m1n2o3p4`; privacy
  export schema stays `1.6`; `EXPORT_COVERAGE` stays at 112.
- Opens are exported through the existing generic `ai_and_ops.app_events`
  contract, with `client_event_id` withheld. There is no second exporter.
- Account deletion removes them with every other `app_events` row.
- Retention is the shared 90-day `app_events` window, pruned by the same
  bounded pruner Step 15 runs after writes.
- No per-user affiliate sub-ID. The tag is one operator constant.

## 13. ODbL

An alternative's barcode may come from Open Food Facts. It is used only to
build the address for the length of one request and is **not persisted**:
telemetry has no barcode field, and nothing in Commerce touches Store A
(test J). The handoff answer carries no Open Food Facts name, brand, grade or
attribution; the card that shows those already renders the attribution.

## 14. Failure behaviour

| Situation | Result |
| --- | --- |
| No partner configured | `unavailable` / `partner_not_configured`; nothing is read |
| No device / unknown device | 401, the Product Result's own refusal |
| Any precondition missing | `not_applicable` with its reason; no link |
| Address fails the registry check | `unavailable` / `unsafe_destination`; no link |
| Handoff read fails in the app | nothing shown; the decision is untouched |
| Answer mismatches the screen | dropped; nothing shown |
| Platform cannot open the link | "The link could not be opened."; no event |
| Event write fails | `{"recorded": false}`; the open already happened |
| Event flood | 429 |

## 15. Non-goals (V1)

No in-app checkout, cart, payment, UPI, card storage or wallet. No order
placement, history, returns or fulfilment. No marketplace, seller onboarding,
seller or sponsored ranking, promoted products or bidding. No brand-paid
placement, certification or safety score. No commission-weighted anything. No
live prices, price comparison, "cheapest" or "best deal", coupons, offers or
stock claims. No scraping, no paid retailer API. No commerce or marketing
notifications, and no Product Watch shopping alert. No advertising or
attribution SDK, retargeting or ad identifier. No lead-generation form. No new
tab, screen or catalogue. No referral code in any link; the share text is
unchanged.

The Constitution permanently rejects **advertising** and **brand-paid
certification**. This is neither: no brand pays for a place, a partner never
chooses or ranks a product, and the link exists only after, and only for, the
engine's own decision.

## 16. Cost

₹0. No payment method, paid service, database, Redis, worker, cron, disk,
domain or Google billing; no new package. The only external party is the
partner's public search page, opened by the person's own device.

## 17. Rollback

Operationally: unset `COMMERCE_PARTNER` and restart. The route then returns
`partner_not_configured` without reading anything, and the app shows nothing.

In code: revert the Step 16 commits. There is no migration, no table and no
Commerce-owned state. Opens already recorded are ordinary `app_events` rows
that age out at 90 days, stay exportable and leave with the account.

## Related

- [`PURCHASE_OPERATING_SYSTEM.md`](PURCHASE_OPERATING_SYSTEM.md) — the decision Commerce reads.
- [`COMPARABLE_ALTERNATIVE.md`](COMPARABLE_ALTERNATIVE.md) — the one alternative.
- [`CONSUMER_GROWTH.md`](CONSUMER_GROWTH.md) — the other `app_events` writer.
- [`PRIVACY_EXPORT_COMPLETENESS.md`](PRIVACY_EXPORT_COMPLETENESS.md) — `ai_and_ops.app_events`.
- [`ODBL_DATA_WALL.md`](ODBL_DATA_WALL.md) — Store A / Store B.
