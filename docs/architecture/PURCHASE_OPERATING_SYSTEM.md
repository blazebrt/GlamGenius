# Purchase Operating System (Step 14)

> Contract `step-14-v1`. Module `backend/app/domains/purchase/operating_system.py`.
> Routes `GET /api/v2/scan/verdict/{barcode}/purchase-check` and
> `GET /api/v2/shopping/candidates/{candidate_id}/purchase-check`.
> Copy `frontend/src/strings/purchaseOs.ts` (`purchase-os-copy.v2`).

## 1. Mission

"Should I buy this?" already has authorities. Step 14 makes them answer as
one system without becoming a new one. For a scanned pack, a Care candidate or
a Fragrance candidate, the Purchase OS returns:

- one current decision (`buy`, `wait` or `skip`) where an authority can
  honestly make one, or a truth state (`not_enough_information`,
  `prohibited`, `unsupported`);
- one primary reason, named by the authority it came from;
- the authorities that mattered, as structured states (not a dump of rows);
- the person's memory, ownership, value and one alternative, as **context**.

It serves the Constitution loop at **DECIDE** (one answer and one reason) and
**REMEMBER** (the person's own earlier decision, with its real fidelity). It
is a read model. It stores nothing.

## 2. What it composes, and what it does not own

| Authority | Owner | How the OS uses it |
| --- | --- | --- |
| Product Result action and reason | `api/v2/product.py::read_product_verdict` | Called in the same request with the same pack ceiling; its `decision.action` is the scan base decision. Never re-graded. |
| Effective physical-pack authority | Product Result (`physical_pack_context` = request ceiling AND server-proven capture) | Read from the Product Result payload. The OS never trusts the query flag on its own. |
| Exact label version | Step 7/12A `LabelSnapshot` (`label_version`) | Identity is `exact` only with a version number and content fingerprint. |
| Official records | Official-records envelope + `validated_revision_heads` + Product Watch's `record_governs_pack` | The only cross-authority rule (§6). |
| Label change | Step 12A `label_change` | Context only. |
| Regulatory change | Step 12B `regulatory_change` on a governed record | Context only. |
| Comparable alternative | Step 6A `alternative` (at most one candidate, ODbL attribution) | Context only. |
| Scan Decision Memory | `product/scan_memory.py` (`step-11c-v1`) | Subject-scoped, exact-version, plus barcode-wide completeness (`barcode_history_coverage`). User outcome only. |
| Exact shelf ownership | Step 10A `InventoryProductLink` via `scan_ownership.status_from_scan` | Context only. |
| Care verdict | `check_service.resolve_care_purchase_check` (`v3-05.7`) | **Is** the Care decision. |
| Fragrance verdict | `check_service.resolve_fragrance_check` (`v3-05.9`) | **Is** the Fragrance decision. |
| Candidate truth | `candidate_truth` / `fragrance_truth` | Decides only whether a candidate's facts are confirmed enough to be checked at all. |
| Purchase guard / Decision Memory | `decision_memory.purchase_guard` (`step-11c-v1`) | Subject-scoped candidate memory, with the recommendation snapshot it actually stored. |
| Strategy registry | `purchase/contract.py` (`v3-05.9`) | Routes a candidate. Unknown fails closed. |
| Decision subject | `family/decision_subject.py` | Read-side resolution only (no `*_for_write`). |

The OS does **not** own: grading, Care policy, Fragrance policy, evidence
selection, official-record matching or ledger integrity, label versioning,
comparable-alternative selection, memory storage, shelf ownership, prices, or
any personal/health context. Each stays with its authority; the OS neither
duplicates nor second-guesses it.

## 3. Audit and gap analysis (before Step 14)

What existed was connected less than it looked.

**Scan / Product Result.** The Product Result route produced `decision.action`,
the exact label version, physical-pack context, the official-records envelope,
`label_change`, and the comparable alternative. Scan Decision Memory and exact
shelf ownership had their own routes and sections. Product Watch had its own
record-governance rule inline. *Gap:* nothing combined them. A matching
current FSSAI record was displayed beside an unchanged BUY — the screen could
say BUY above a regulator's recall for the pack in hand.

**Candidate purchase.** Care (assessment, evidence, value, verdict) and
Fragrance (truth, check, verdict) were complete, with inspection,
confirmation, one-tap decisions, the purchase guard and subject-scoped
Decision Memory. *Gap:* the customer screen that reached them,
`frontend/app/shopping-check.tsx`, had been retired to `<Redirect href='/scan' />`
in the pivot lock. The Care and Fragrance experience was unreachable dead UI.
Legacy supplement or clothing candidate rows can still exist
(`ShoppingCandidate.category` has no database constraint); nothing routed
them safely in one place.

**Ownership.** `InventoryItem` plus `InventoryProductLink` prove exact
scan ownership; Care's `decision_context` and Fragrance's `collection_context`
carry their own owned-context. *Gap found, not changed by Step 14:* Step 10A
lists `beauty`, `hair` and `perfumes` as shelf-eligible, but the skin-care
capture route binds `product_category = "skin_care"`, and the food label
route carries no category. So a pack confirmed through today's capture routes
is never shelf-eligible from a scan; the OS reports `not_eligible_for_shelf`
honestly. Aligning the two is a Step 10A/8J decision, not a Purchase OS one.

**Frontend.** Product Result, Scan Decision Memory, shelf ownership, Better
Option, and the Care/Fragrance components existed. *Gaps found:*

1. `shopping-check.tsx` redirects to Scan, so candidate checks had no entry.
2. **Fixed in Step 14 (review correction):** since `27f1df4` the Product
   Result route requires `X-Device-Token` (`current_device`), but the app's
   `readProductVerdict` (`frontend/src/services/apiV2.ts`) sent no device
   header, so a real Product Result read from the app was refused with
   `401 DEVICE_UNKNOWN` — before the Step 14 section could ever mount. The
   uncredentialed reader is removed; the screen now reads through
   `productScan.readDeviceProductVerdict` (§18). The backend route is
   unchanged and still requires the device.
3. The reused Care/Fragrance result components predate the keyed-copy rule
   and still carry inline English. Step 14 reuses them unchanged and adds no
   words to them; its own files are keyed and guarded.

## 4. Strategy routing

| Path | Adapter | Decision |
| --- | --- | --- |
| Scan | `scan_purchase_check` | Product Result action, then the official ceiling (§6). |
| `care_purchase` (`beauty`, `hair`) | `_care` | The canonical Care verdict, unchanged. |
| `fragrance_purchase` (`perfumes`) | `_fragrance` | The canonical Fragrance verdict, unchanged. |
| `supplement_purchase` (`supplements`) | `_supplement` | `prohibited`. No check runs; no fallback. |
| Anything else | `_unsupported` | `unsupported`. Never Care by default. |

`CANDIDATE_ADAPTERS` is a literal read-only table — no reflection, no
category-name dispatch. A strategy with no adapter, or in any state other than
`active`/`prohibited`, is `unsupported`. Another account's candidate is the
same not-found as an invented id.

Before calling a canonical check, Care and Fragrance confirm the candidate's
facts are trusted (`user_declared` or `confirmed`). A draft read from a photo
is `not_enough_information` with `candidate_confirmation_required`; a
fragrance candidate whose details cannot be validated is
`candidate_details_unsupported`. Neither calls the check.

## 5. The common contract (`step-14-v1`)

```json
{
  "contract_version": "step-14-v1",
  "context": { "kind": "scan | candidate", "strategy": "scan_product | care_purchase | fragrance_purchase | supplement_purchase | null", "category": "..." },
  "subject": { "household_subject_id": "... | null", "is_account_holder": true },
  "identity": { "state": "exact | insufficient", "...": "strategy-safe public identity" },
  "decision": {
    "state": "decided | not_enough_information | prohibited | unsupported",
    "verdict": "buy | wait | skip | null",
    "primary_reason_code": "...",
    "primary_reason_authority": "product_result | official_records | care_purchase | ...",
    "decision_fingerprint": "..."
  },
  "authorities": [ { "authority": "...", "status": "...", "effect": "context_only" } ],
  "memory": { "kind": "...", "fidelity": "user_outcome_only | recommendation_snapshot", "state": "..." },
  "ownership": { "...": "only what the ownership authority proves" },
  "alternative": { "status": "...", "reason_key": "...", "candidate": { } },
  "value": { "...": "strategy-specific, or state: missing" },
  "boundary": { "code": "supplement_purchase_prohibited", "redirect": "supplement_label_utility" },
  "missing_information": []
}
```

`verdict` is present only when `state` is `decided`; `_decision` raises if
anything else is attempted. Scan identity carries the barcode, label version
number and content fingerprint (already contract-integrity fields of the scan
routes), plus `physical_pack_context` and `reference_view`. Candidate identity
carries `exact`/`insufficient`, the guard's identity version, and the display
name and brand the person entered. No account id, candidate id, inventory id,
label-snapshot id, AI run id, media id, storage key or revision id leaves the
server (backend test AD).

## 6. Decision precedence

Scan (explicit policy in `compose_scan_decision`, not arithmetic):

1. No exact label version → `not_enough_information` / `identity_insufficient`.
   A decision is never manufactured from a barcode.
2. A governed exact official record under physical-pack authority → the
   **WAIT ceiling**:

   | Product Result | Purchase OS |
   | --- | --- |
   | `buy` | `wait` (reason `official_record_matches_pack`, authority `official_records`) |
   | `wait` | `wait` (the Product Result's own reason) |
   | `skip` | `skip` (the Product Result's own reason) |
   | none (not graded / not enough information) | `wait` (reason `official_record_matches_pack`) |

3. Otherwise the Product Result action, unchanged — or
   `not_enough_information` when it has none.

Care → the Care verdict. Fragrance → the Fragrance verdict. Supplements →
`prohibited`.

**Why only the official record may override a scan in V1.** A changed formula,
a changed regulatory history, a better alternative, owning the exact pack, or
having chosen differently before each matter — and each means different things
in different contexts. The Product Result already evaluates the current pack.
A current regulator's record for the exact pack in hand is the one signal that
by itself says "do not buy this now". Care and Fragrance weigh their own
analogues inside their canonical verdicts; the OS does not weigh them again.

### Governed means fully governed

The ceiling applies only when **all** hold (`product.watch.record_governs_pack`,
shared with Product Watch so the two cannot drift):

- the Product Result granted physical-pack authority (request ceiling AND a
  server-proven capture on this device) and the identity is exact;
- the record came through the exact licence-and-lot matcher
  (`match_state == "matched"`); ambiguous candidate sets are withheld by the
  matcher and never reach the OS;
- its Step 12B revision ledger validates (`validated_revision_heads`); a
  corrupt ledger yields no heads and so no ceiling;
- its source is the openable FoSCoS page, which the authority chain carries as
  `{"name": "FSSAI / FoSCoS", "url": "https://foscos.fssai.gov.in/food-recall"}`.

Open Food Facts cannot create an official match (backend test L). A reference
view never gets one (test M).

### Omission never clears it; nothing is invented to release it

The Constitution asks the manager to give things back when a constraint lifts.
Step 14 does not fake that. There is no reviewed rule today that says an
omitted record is cleared, that a termination date means safe, or that some
status text means the restriction lifted. So a record's absence from a later
export, a termination date, or status text changes nothing (tests N). A future
explicit relieving-state authority, under review, can lift the ceiling; until
then absence alone changes nothing, and the copy never promises when it ends.

## 7. Context-only signals

| Signal | Where it appears | Effect |
| --- | --- | --- |
| Formula / label change (Step 12A) | `authorities[label_change]` | None (test O). |
| Regulatory change (Step 12B) | `authorities[regulatory_change]`, only on a governed record | None (test P). |
| Comparable alternative (Step 6A) | `alternative` — one candidate or none | None (test Q). A better option does not make a good product bad. |
| Exact ownership | `ownership` | None (test R). |
| Prior decisions | `memory` | None (tests S, V, override tests). |

## 8. Ownership

Scan: only an `InventoryProductLink` for this exact label version, read
through `status_from_scan` (states `owned_exact_version`, `not_owned`,
`not_eligible_for_shelf`, `physical_pack_required`, `reference_view`,
`sign_in_required`, `identity_insufficient`). A "bought" decision is memory,
never ownership: recording BUY creates no inventory row (test S), and the
candidate screen never calls a shelf write (frontend test).

Care: `role_status` and `eligible_owned_same_slot_count` from Care's own
`decision_context` — context the Care verdict already used. Fragrance:
`owned_perfume_count`, `exact_owned_count`, `same_family_owned_count` from
Fragrance's `collection_context`. Owning something does not by itself mean
"do not buy"; that judgement stays inside each strategy.

## 9. Decision Memory

Subject-scoped throughout: the account holder and each household member see
only their own decisions (tests V). A named `subject_id` without a signed-in
account is refused rather than read as the device owner.

**Scan memory fidelity: `user_outcome_only`.** Scan Decision Memory stores the
person's BUY/WAIT/SKIP and the exact version it was about. It stores no
historical recommendation, so none is reported and none is reconstructed from
today's rules (test X); the app never says "you bought this even though
GlamGenius said Skip". A decision about an earlier label version is reported
separately as `earlier_version_decision` with `applies_to_current_version:
false`, and is never shown as the current decision (test Z; frontend test).

**Completeness is barcode-wide and its own fact.** Scan memory reports three
separate things, and never folds one into another:

1. the exact current decision (`read_scan_memory`, this exact label version);
2. the latest *attributable* decision on another version
   (`latest_decision_on_another_version`, under `subject_row_filter`);
3. whether this subject's history for the whole barcode is complete
   (`scan_memory.barcode_history_coverage`).

An unattributed legacy decision — subject-less and written after the household
existed — is nobody's (Step 11C). The exact-version read alone cannot see one
on an *older* version, and the other-version read correctly excludes it, so
without (3) a history that silently lost it would look complete. The helper
re-derives the subject, checks `ambiguous_legacy_filter` for the account and
barcode across every label version, and returns completeness metadata only; the
ambiguous row is never returned, counted for display or adopted by anyone
(tests W: older ambiguous row, named member, pre-household legacy row, other
barcode).

`history_complete` is false whenever either the exact version or the barcode
has unattributed history. `state` is `prior_exact_decision` when an exact
current decision exists — a known decision is never hidden — and
`history_incomplete` only when there is none; the two can coexist, and the app
shows the incomplete-history line whenever `history_complete` is false, beside
the exact decision if there is one.

**Candidate memory fidelity: `recommendation_snapshot`.** Candidate Decision
Memory stored the recommendation at decision time, so it is reported as
recorded then — never recomputed with today's policy (test Y). The guard's own
state is carried verbatim as `guard_state` for the existing memory card.

Different histories have different fidelity, and the contract says which.

A one-tap override stays one tap and never changes engine truth: recording a
decision changes neither the scan decision nor the candidate verdict
(override tests), and does not re-run the canonical check.

## 10. Value

Strategy-specific. Care reports its own `candidate_spend_status`,
`owned_value_recovery_status` and `currency_context_status`. Fragrance reports
whether a candidate price was recorded. A scanned pack's price is `missing` —
Step 14 builds no price engine, scrapes nothing, queries no retailer, infers
no market price, converts no currency.

## 11. Evidence chain

`authorities` lists only what is relevant to the path, as states. Scan:
`product_result`, `official_records`, optionally `regulatory_change`,
`label_change`, `ownership`, `decision_memory`, `alternative`. Care: the Care
strategy (with its supporting reason codes), `care_evidence`, ownership and
memory. Fragrance: the strategy, ownership and memory. A negative regulatory
statement carries its openable FSSAI source; Care keeps its reviewed evidence
chain; the Product Result keeps its own source chain. No internal rule id is
ever presented as a source.

## 12. Reference view

`physical_pack_context=false` (opening a comparable alternative) is not
holding the pack. The OS still reads the Product Result's decision, but
official status, ownership and memory are `reference_view`, the ceiling is
never applied, and the app shows only a line saying pack-specific context
appears after a scan. The frontend refuses the ceiling in a reference view
even if a payload claimed otherwise.

## 13. No score, no commerce, no growth

There is no purchase, confidence, suitability, overall or weighted score, and
no arithmetic combining grade, price, ownership, evidence, personal context,
alternative or regulation (tests AA, including an AST check that the module
does no arithmetic). No cart, buy-now, retailer or affiliate link, coupon,
offer, live price, stock, seller ranking, payment, checkout, order tracking,
marketplace or retailer API (test AB and the frontend copy test). No
referrals, invites, social growth loops, campaigns or acquisition analytics.

Step 16 keeps all of this true of the Purchase OS itself. Its disclosed
outbound link is a separate, downstream layer that reads the finished answer
and is never read back: see [`COMMERCE_HANDOFF.md`](COMMERCE_HANDOFF.md).

## 14. Free and paid packaging

Nothing is gated today and no entitlement model is built. For later
packaging, by the Constitution's free/paid split:

- **Free forever:** the scan decision and its reason, the official-record
  ceiling and its source, the authority chain, the one comparable
  alternative, the candidate's canonical Care/Fragrance verdict.
- **Eventually paid:** `memory` (history), `ownership` (shelf tracking),
  household `subject` selection (family profiles), and Care's owned-value
  context (the skin and hair manager).

Nothing currently free may be removed.

## 15. Privacy, ODbL and persistence

No table, column, cache or snapshot; no migration; Alembic head unchanged. The
read writes nothing — a digest of every row of every table is identical
before and after all five route calls (test "reads write nothing"). Because
nothing is persisted, privacy export and deletion need no new domain; each
underlying source is already exported and erased through its own domain.

The OS does not import Store A. The only Open Food Facts material in a
response is the comparable alternative's public fields and attribution, which
the Product Result already joined at query time; nothing combined is stored
(test AC; `ODBL_DATA_WALL.md`).

## 16. Concurrency

The read takes no lock. Beyond the source checks, a test captures every SQL
statement the two routes send (scan self and member, Care, Fragrance for a
member) and asserts none contains `FOR UPDATE`, `FOR NO KEY UPDATE`,
`FOR SHARE`, `FOR KEY SHARE` or an advisory lock. A read that takes no lock
cannot join, and so cannot invert, the documented orders:

- candidate decision write: `Account FOR KEY SHARE → PurchaseDecision FOR UPDATE
  → subject → ShoppingCandidate → PurchaseDecisionEvent`
  (`decision_memory.py`);
- subject resolution for writes: account holder `Account FOR UPDATE`; member
  `Account FOR KEY SHARE → FamilyProfile FOR UPDATE` (`decision_subject.py`);
- label confirmation: `lock_label_version` (advisory, per barcode);
- Product Watch writes and official-record ingestion keep their own locks.

A read racing a write sees either side of it, and the app re-reads the
purchase check after a one-tap decision so "your prior decision" is never
stale. Subject resolution on this path is the read-side `canonical_subject`.

## 17. Failure modes

| State | When |
| --- | --- |
| `not_enough_information` / `identity_insufficient` | No confirmed label version for the scan. |
| `not_enough_information` / the Product Result reason | Not graded, or not enough label facts. |
| `not_enough_information` / `candidate_confirmation_required` | A candidate's facts are still a draft. |
| `not_enough_information` / `candidate_details_unsupported` | A fragrance candidate's details fail validation. |
| `physical_pack_required` | Official/ownership context without a proven capture. |
| `history_incomplete` (and `history_complete: false`) | Unattributed legacy history exists for this subject on any version of the barcode; with an exact current decision, `history_complete` is still false. |
| `prohibited` / `supplement_purchase_prohibited` | Supplements, with the label-utility redirect. |
| `unsupported` / `unsupported_strategy` | Any category the registry does not route. |

WAIT is a purchase decision; not-enough-information is a truth state. The only
place one becomes the other is the governed official-record ceiling. In the
app, a failed purchase check hides the section and never the verdict.

## 18. Frontend

- **No new tab.** Scan and You remain the only primary tabs.
- **Product Result.** A "Your purchase context" section directly beneath the
  decision, rendering only rows whose authority has something to say: why
  this decision, official record (with its openable source), what you already
  own (only when proved), your prior decision (earlier versions labelled as
  such), verified product changes (marked as context), and a pointer to the
  one comparable option already shown below. It never repeats the verdict.
  The dominant block changes only for the governed official ceiling, where it
  shows the ceiling's answer and reason so the screen never shows BUY above
  a section explaining why the answer is not BUY. A check is used only when it
  describes the exact barcode, version, fingerprint and view on screen.
- **Product Result is read as this phone.** `verdictClient.getProductVerdict`
  calls `productScan.readDeviceProductVerdict`, which sends the stored
  `X-Device-Token` and keeps `physical_pack_context=false` for reference views.
  It reads the stored device and never registers one: opening a result must
  not mint an identity. With no stored credential no request is sent and the
  screen shows its ordinary failure state; there is no device-less Product
  Result to fall back to. The Step 14 purchase check uses the same stored
  device.
- **Secondary entry.** On the scanner, below "Point at a barcode", signed-in
  people see "Can't scan it? Check a product you're considering", leading to
  `/purchase-candidate`. That screen says scanning first, offers only the
  registry's categories, and routes on the server's Purchase OS answer.
- **No free text (Constitution: structured dropdowns only).** The candidate
  screen has no `TextInput` and never sends `source: "manual"`; the legacy
  manual API stays for compatibility but is not exposed here. The flow is:
  category → photo or screenshot → the extracted facts → **confirm** or **read
  another photo** → the canonical Care or Fragrance result. A wrong read is
  answered with another photo, never an editor: the review cards' correction
  button is not offered. Care is confirmed exactly as read (an empty
  confirmation body); a Fragrance read keeps every extracted fact and only its
  structured occasions and seasons, from the server's own options, are set.
  Provenance stays photo-extracted. No price is typed; Care already handles a
  missing candidate spend. Supplements get the prohibited boundary instead of
  any purchase flow. No search, catalogue or feed. `shopping-check.tsx` stays
  the pivot-locked redirect. Structural tests fail on any `TextInput`, any
  `manual` source, or any correction path in this screen.
- **Accessibility.** The dominant block's label carries the decision word, and
  the "why" row reads "Purchase answer: WAIT. An official record matches this
  exact pack." — decision and reason in words, never colour alone.

**Cost note.** The Product Result screen reads the Product Result and then the
purchase check, which rebuilds the Product Result server-side; the candidate
screen's decided path reads the purchase check and then the canonical check
for its full result card. Each is one extra read of existing, non-AI work.
Folding them is a later optimisation and must not create a cache table.

## 19. Rollback

Revert the Step 14 commit. There is no migration, no data and no persisted
state to unwind; the two routes are additive; existing routes (Product Result,
care-check, fragrance-check, candidate decision, scan memory, purchase guard)
are unchanged. The app treats a missing purchase check as "no section".

## Related

- [`COMPARABLE_ALTERNATIVE.md`](COMPARABLE_ALTERNATIVE.md) — the one alternative.
- [`PURCHASE_DECISION_MEMORY_GUARD.md`](PURCHASE_DECISION_MEMORY_GUARD.md) — candidate memory and its snapshot.
- [`LABEL_CHANGE_AUTHORITY.md`](LABEL_CHANGE_AUTHORITY.md) — Step 12A label versions and change.
- [`REGULATORY_CHANGE_AUTHORITY.md`](REGULATORY_CHANGE_AUTHORITY.md) — Step 12B ledger and change.
- [`PRODUCT_WATCH_MATERIAL_NOTICES.md`](PRODUCT_WATCH_MATERIAL_NOTICES.md) — the shared record-governance rule.
- [`SUPPLEMENT_AUTHORITY.md`](SUPPLEMENT_AUTHORITY.md) — the supplement label utility the boundary points to.
- [`ODBL_DATA_WALL.md`](ODBL_DATA_WALL.md) — Store A / Store B.
- [`COMMERCE_HANDOFF.md`](COMMERCE_HANDOFF.md) — Step 16, downstream of this contract.
