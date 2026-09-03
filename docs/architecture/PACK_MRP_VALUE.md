# Observed pack MRP

## What this feature claims, exactly

One sentence, and everything below exists to stop it growing:

> Two confirmed pack labels recently stated these maximum retail prices, and
> this is what they work out to per 100 g.

It is **not** a price. It is not a saving, not a value score, not a cheaper
option, not affordability advice, and not a market comparison. We looked at two
photographs somebody confirmed. That is the whole evidence base.

## MRP is not a selling price

Maximum Retail Price is the most a pack may legally be sold for in India,
printed on the pack by the manufacturer. It is not what a shop charges — almost
nothing sells at MRP — and the app has no evidence whatsoever about discounting.

So no surface may say *price today*, *current price*, *selling price*, *what you
will pay*, *store price* or *deal price*. It says **MRP**, and it says when the
pack was read.

## Quality and money never combine

There is no `value_score`, no `health_per_rupee`, no `grade_per_rupee`, and no
arithmetic anywhere that folds a letter and a number of rupees into one figure.
The Constitution rejects a single composite score averaging incompatible things,
and quality-per-rupee is the purest example of one. A structural test enforces
that these names do not exist rather than merely being hidden from the API.

## Price runs after the choice, and cannot reach back

This is the invariant the whole design rests on, and it is enforced by ordering
rather than by discipline:

```
Product Result science
  -> Official Records
  -> Community
  -> Step 6A picks zero or one alternative, on science alone
  -> Step 6B reads that decision and prices the two products
```

`pack_mrp_value_envelope()` takes the already-computed alternative envelope as
an input. The candidate barcode and the comparison basis both come *out* of it.
There is no code path from a price back into selection, because by the time any
money is read the candidate is decided and this domain has no way to ask for a
different one.

**No price-aware fallback.** If Step 6A picks an A-graded product with no
readable MRP while a B-graded one is priced and cheap, the answer is: the A is
still the alternative, and the MRP comparison is not available. Tests seed
exactly that temptation.

## The AI transcribes. Code decides

`mrp_text` is a new optional field on the label extraction schema. The model
copies the MRP clause **word for word** — `"MRP ₹120"`, `"M.R.P. Rs. 99.00"` —
and is told explicitly that a rupee symbol beside a number is not enough, that
an offer or selling price is not an MRP, and that an unreadable price goes in
`uncertain_fields` rather than being guessed.

Everything after that is deterministic: parsing, normalising, dividing and
comparing are code that fails closed. The model never decides what something
costs, whether it is good value, or that a declaration exists.

Computing a comparison makes **zero AI calls**, proved by a test that raises if
the gateway is touched.

## Schema v2, and a review somebody already started

The extraction schema and prompt move to `scan-label.v2`. Confirmation accepts
**both** `scan-label.v1` and `scan-label.v2`.

The reason is a person: they photograph a label, walk to the till, and tap
confirm after a deployment happened in between. Refusing their review because a
version string moved underneath them throws away work they already did.
`mrp_text` is optional in both directions, so a v1 payload validates without it.
New transcriptions are only ever produced at v2.

**The run and its output must agree.** Widening the accepted set does not widen
what may disagree: `run.schema_version == output.schema_version`, *and* that one
agreed version must be confirmable. A v1 run carrying a v2 output is refused
even though each half is individually acceptable, because we could not then
state which schema produced the facts we were about to store. Production writes
no such pair; the check exists so that if one ever appeared it would stop rather
than be absorbed. The refusal is total — no scan event, no snapshot, no product
record, no moved verification status.

## A price change is not a reformulation

`mrp_text` is deliberately **absent** from `CONTENT_FACT_FIELDS`,
`canonical_label_facts()`, `label_content_fingerprint()` and
`label_changed_fields()`.

Semantic label versioning tracks what is *in* the pack. Repricing the same
recipe must not look like a reformulation, or every price rise would read as a
changed product and every shopper would be told the formula moved.

So: photograph the same formula at ₹100 and again at ₹110, and there is still
one `LabelSnapshot` at version 1 with an unchanged fingerprint — and two scan
events, each keeping its own price.

## Why the scan event is the commercial authority

Directly because of the rule above. Step 3 deduplicates equal semantic label
content, so the second capture legitimately reuses the first snapshot. If only
the price changed, **that snapshot still holds the old MRP**.

Reading `LabelSnapshot.facts` for money would therefore publish ₹100 for a pack
that now says ₹110. The exact confirmed `ScanEvent.label_facts` is the
commercial observation; the snapshot is not.

Both the MRP and the net quantity come from the **same** capture. Pairing a
price from one photograph with a pack size from another describes a pack that
never existed.

## Latest means latest

For one barcode, the observation is the newest confirmed label capture, ordered
by `created_at` then `id` — both written by the database. A phone's `scanned_at`
is a client claim and is never consulted.

Exactly one row is read, and there is no looking further back. If the newest
capture could not read a price, or states a pack size we cannot parse, the
truthful answer is that we no longer have a usable observation — not that an
older photograph once said something convenient.

Only a real confirmed capture counts: `outcome == label_captured` with a
dictionary of label facts. A plain barcode scan saw no pack and cannot state a
price.

## Freshness is our policy, and its own policy

`MRP_OBSERVATION_MAX_AGE_DAYS = 30`, on the server's clock. Strictly less than
the window is fresh; exactly the window is already stale. An observation with no
timestamp is not fresh.

This is a GlamGenius comparison rule, not a claim that a printed MRP legally
expires — packs are repriced and reprinted, and repeating a two-month-old
reading as though it described the shelf asserts something we cannot know.

It is deliberately **not** the Open Food Facts cache window. The two are the
same length today and answer different questions — *how old is our copy of
somebody's database* versus *how old is our reading of a physical pack* — so
they are separate constants that can move apart. A test forbids the value domain
from importing the OFF one.

## The parsers refuse more than they accept

A wrong MRP is worse than no MRP, and the honest answer costs a shopper nothing.

**MRP.** NFKC, collapse whitespace, casefold. Then an explicit marker is
required (`MRP`, `M.R.P.`, `Maximum Retail Price`) *and* an explicit rupee
indication (`₹`, `Rs`, `Rs.`, `INR`). Exactly one currency-anchored amount, no
range following it, positive, finite, at most two decimal places. Everything
else returns nothing:

| Accepted | Refused |
| --- | --- |
| `MRP ₹120` | `₹120` — no declaration |
| `M.R.P. Rs. 120.00` | `Offer ₹99`, `Selling price ₹99` |
| `Maximum Retail Price INR 75` | `MRP ₹100 / ₹120`, `MRP ₹100-120` |
| `MRP: ₹1,299/-` | `MRP 0`, `MRP ₹0`, `MRP -10`, `MRP FREE` |
| `Maximum Retail Price ₹125 incl. of all taxes` | `MRP ₹99.999`, `Approx ₹100`, garbage |
| `MRP (incl. of all taxes): ₹120` | `MRP not printed. Offer ₹99` |
| `MRP ₹1,299`, `MRP ₹1,29,999`, `MRP ₹1,234,567` | `MRP ₹1,2,3`, `MRP ₹1,,299` |

**The amount must belong to the declaration.** Only separators and a
parenthesised aside may sit between the words "MRP" and the number. That aside
exists because `MRP (incl. of all taxes): ₹120` is how a great many Indian packs
are printed, and it may contain neither a digit nor the word "not", so it cannot
swallow the rule it is an exception to. This is what makes
`MRP not printed. Offer ₹99` fail closed: the ₹99 is a shop's asking price in
the next sentence, and publishing it as a maximum retail price would put a
number on the pack that the pack does not carry.

**The digit grouping must be one that exists.** Ungrouped (`1299`), Western
(`1,299`, `1,234,567`) and Indian lakh grouping (`1,29,999`) are read; anything
else is refused *whole*. The failure worth naming is the quiet one — a parser
that strips commas reads `₹1,2,3` as 123, and one that salvages a prefix reads
it as ₹1.

There is **no market-price ceiling**. A 25 kg sack is not suspicious for costing
what a 25 kg sack costs, and a bound picked from a guess about grocery prices
would silently drop exactly those packs. The only limits are technical ones on
the input *string*: its length, and a `Decimal` context derived from that length
which traps rather than rounds. A test asserts past where the old ceiling stood.

**Quantity.** Anchored at both ends, so the whole string must be a quantity —
a pattern that merely searched would happily read `500 g` out of
`100 g + 20 g free`. `500 g`, `1 kg`, `250 ml`, `1 L`, `4 x 25 g` all parse;
`approx 500 g`, `10 pieces`, `12 sachets`, `family pack`, `500 g / 550 g` do
not. Never inferred from a product name.

A dedicated parser, not the existing `pack_size_g()`: that function treats
millilitres and grams as one numeric size for a different presentation job, and
a price denominator has to keep its physical dimension.

## Mass with mass, volume with volume

`per_100g` requires a mass quantity on **both** sides; `per_100ml` requires a
volume on both. A gram-to-millilitre conversion needs a density that no pack
prints, so where the dimensions disagree the comparison is simply unavailable.

## Decimal, and one rounding

Every figure is an exact `Decimal`. Binary floating point cannot represent 0.10,
and a price that drifts by a paise per arithmetic step is a price we cannot
defend in front of anybody.

```
mrp_per_100 = mrp_rupees * 100 / base_amount
```

Rounding happens **once**, in `quantize_money()`, `ROUND_HALF_UP` to ₹0.01 —
and it happens **before anything is concluded**. Both per-100 figures are
quantised first, and the relationship and the difference are then derived from
those quantised figures.

The other order is the tempting one, and it is wrong. Comparing the exact
quotients and printing the rounded ones produces a card that shows ₹24.00
against ₹24.00 and calls one of them higher, or shows figures a paise apart and
reports the difference as `-0.00`. Both are discrepancies a shopper can see in
the numbers in front of them, and no amount of twelfth-decimal-place accuracy
answers them. Whatever the card says about money must be the subtraction a
reader could do by hand.

Money crosses the wire as decimal strings (`"120.00"`, `"24.00"`, `"-4.00"`),
never JSON floats, and the app formats them without ever doing arithmetic of
its own.

The difference is defined once: **candidate minus current**. Negative means the
candidate's MRP per 100 is the lower of the two.

## Why both the pack and the per-100 figure are shown

Because they can say opposite things, and the absolute one is the more
persuasive:

```
Current      MRP ₹120 · 500 g      MRP per 100 g ₹24
Alternative  MRP ₹100 · 400 g      MRP per 100 g ₹25
```

₹100 looks cheaper than ₹120 right up until the packs are normalised. Showing
only the normalised figure would let somebody conclude the opposite of what the
arithmetic says; showing only the absolute one would be worse. Both, always.

## The words

The heading is **MRP comparison** — never Best value, Cheapest, Better value,
Smart value, Worth it or Value winner.

The surface reports numbers and does not characterise them. No *save ₹4*, no
*17% cheaper*, no *good value*, no *overpriced*, no *budget choice*, no green
winner and no celebratory badge. The relationship vocabulary is arithmetic in
words: `candidate_lower_mrp_per_100`, `same_mrp_per_100`,
`candidate_higher_mrp_per_100`.

Every observation is dated — "Observed on a confirmed pack · 2 Sep 2026" —
because an MRP changes and an undated reading is a claim we cannot support. The
missing line is "Not enough recent pack information to compare MRP.", never "no
price found": we did not search anywhere.

## No new table, no migration, no cache

V1 adds no persistence. The confirmed `ScanEvent` already holds the barcode, the
account and device provenance, the server timestamp, the confirmed facts and the
AI run — which is everything an observation needs.

Every request recomputes from live retained scan events. Nothing about the
public envelope is cached, which is also what makes withdrawal and deletion work
without any special handling: when a capture goes, the next request simply reads
whatever is newest now.

## Privacy, and what a deletion takes with it

The observation lives inside an account-owned scan event and follows that
lifecycle exactly. The privacy export already serialises every `ScanEvent`
column, so `mrp_text` travels with the person's own data — and never with
anybody else's.

Deleting an account removes its captures through the real deletion state
machine. If a remaining capture then becomes the newest, it may be used — that
is the ordinary latest-capture rule, not a reach behind the deletion. Nothing
retains the deleted account's observation anywhere, because nothing cached it.

The public envelope names a product and a date. Never an account, a device, a
scan event, an AI run, or who captured it.

## No retailer, ever

Zero external price requests: no retailer API, no web call, no scraping, no
affiliate, no commerce provider, no new HTTP client. A structural test forbids
the domain from importing one or carrying a URL.

And no manual entry. There is no text box, no "enter MRP", no "what did you
pay", no receipt OCR and no screenshot parsing. This milestone concerns printed
pack MRP that a person confirmed, and nothing else. What somebody actually paid
is a later purchase-memory question.

## Reference view

The MRP comparison may still appear when an alternative is opened as a
reference, because it is explicitly a *dated observation from a confirmed pack
label* rather than a claim about the pack in the viewer's hand.

Step 6B loosens none of the reference-mode restrictions: exact pack Official
Records stay suppressed, batch Community signals stay suppressed, and
report-what-you-saw stays replaced by an invitation to scan.

## Nothing here moves a verdict

MRP never changes BUY, WAIT or SKIP. A product does not become BUY for being
inexpensive or SKIP for being dear. Reasoning about whether a purchase is
necessary is Purchase Guard's job, later.

## Where it lives

| Piece | File |
| --- | --- |
| MRP and quantity parsers | `backend/app/domains/value/parsing.py` |
| Freshness, rounding, relationship, reason keys | `backend/app/domains/value/policy.py` |
| Commercial authority and the comparison | `backend/app/domains/value/service.py` |
| The transcribed clause | `backend/app/domains/product/extraction.py`, `mrp_text` |
| Where it joins the Product Result | `backend/app/api/v2/product.py`, `read_product_verdict` |
| The surface | `frontend/src/components/verdict/MrpComparison.tsx` |
| Every word it says | `frontend/src/strings/verdict.ts`, `S.mrpComparison` |
| Backend tests | `backend/tests/test_step6b_pack_mrp_value.py` |
| Frontend tests | `frontend/src/__tests__/mrpComparison.test.tsx` |
