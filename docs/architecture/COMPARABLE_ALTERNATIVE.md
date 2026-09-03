# The comparable alternative

## What this feature claims, exactly

One sentence, and the whole design follows from how narrow it is:

> Open Food Facts lists another product in the same category, and under the
> same rules it grades higher.

That is two facts and a citation. It is deliberately **not** "here is a
healthier food", "here is what you should eat", "here is the best product in
this category", or "here is the cheapest one". Each of those is a claim we
cannot support, and three of them are claims the Constitution forbids outright.

The question the engine answers is *"is there a product in the same kind of
category that GlamGenius can defensibly compare this one with?"* — nothing
larger.

## Packaged food only

Food has a production grading framework, so a comparison between two food
products can be made deterministically and shown with its working. Cosmetics,
hair care and supplements do not have one yet; comparing them needs the shared
substance layer from a later milestone.

So this milestone covers the packaged-food Product Result path and fails closed
everywhere else. Faking comparability across domains would be worse than
silence.

## Two stores, two questions, and neither may answer the other's

This is the distinction the whole design rests on.

**Open Food Facts (Store A) says which products *might* be comparable.** The
category the source publishes, the countries it lists, and how recently we
copied the row. That is its whole remit here.

**A confirmed label snapshot (Store B) says what a candidate actually *is*.**
Its name, its brand, its ingredients, its panel — and the one thing the
catalogue cannot tell us: whether that panel was printed per 100 g or per
100 ml.

The second half is not a nicety. `from_scan.basis_for()` decides "drink"
because a category or a product name contains the word *milk*, and that guess
is fine for choosing which threshold table to read. It is not fine as a
published statement about how two products were compared. A card that reports
`basis: per_100g` has to know it, and only a person who read the pack does.

So a candidate with no current confirmed label is **not offered**, however
complete its catalogue entry looks. Returning no candidate is the right answer;
inferring the panel basis is not. The same requirement applies to the product in
the shopper's hand, because a comparison has two sides and a stated basis has to
be true of both — a catalogue-only product still gets its ordinary verdict, it
simply gets no comparative claim.

## The chain, in order

```
current graded product, from its own confirmed label
  -> canonical category key, from the Open Food Facts row's taxonomy tags
  -> that row's copy must be inside the freshness window
  -> PAGED reads of cached Store A candidates, every gate answered in SQL:
     same canonical category, canonical India tag, dated, fresh, not itself
  -> ONE batch read of each page's latest label snapshots from Store B
  -> the same adapter, the same grader, the same resolved ruleset
  -> strictly higher grade, no worse a decision, the same printed panel basis
  -> a name a person can read
  -> lexicographic selection: grade, then action, then barcode
  -> at most ONE candidate on the public contract
```

`backend/app/domains/alternatives/` owns it: `category.py` states the
comparison rule and defers to `app/domains/off/taxonomy.py`, which is where the
source's field semantics are documented, `policy.py` holds the eligibility,
ranking and work-budget rules, `service.py` runs the chain. The domain consumes
the grading, Open Food Facts, product-label and presentation layers; it owns
none of them.

## Category comparability is not a scientific claim

Two products are comparable when Open Food Facts publishes the **same complete
category classification** for both: their `categories_tags` taxonomy arrays,
normalised and compared as whole sets.

### Why not the `categories` text field

Because Open Food Facts says not to. Their published product schema describes it
verbatim as:

> Comma separated list of categories (**not taxonomized**), in the last language
> used to edit it (recorded in categories_lc). This field is mostly used for
> **debugging and testing purposes**. Do not use it for display purposes.

Two consequences follow, and both are silent failures rather than errors:

* **The same product kind stops matching itself.** A cereal last edited by a
  French contributor reads `Céréales pour petit-déjeuner`; the same kind of
  product edited in English reads `Breakfast cereals`. Comparing the text finds
  nothing and a perfectly good alternative is never offered.
* **Different products start matching.** A cereal bar whose contributor happened
  to type `Breakfast cereals` as the final element collides with a box of
  cereal, and a comparison gets published between two things nobody classified
  together.

`categories_tags` is the array whose taxonomy-matched entries are exact
canonical ids of the form `en:breakfast-cereals`, independent of the editor's
language.

### Why the whole set, and not "the most specific tag"

Nothing in the published schema says `categories_tags` is ordered
broadest-first. Calling the final entry the leaf is an assumption dressed as a
reading, so no single element is picked at all: the key is the sorted set of the
whole array. That is order-independent by construction and is the narrowest
comparison their documented semantics support — two products match only when
their entire published classification is identical.

The lossiness their schema warns about applies to entries that matched *no*
taxonomy entry. Because the whole set must be identical, such an entry has to be
byte-identical on both sides before it can contribute to a match. It can cost us
a match; it cannot manufacture one. That is the only direction this may fail in.

`compared_to_category` appears nowhere in the published product schema, so it is
not part of the documented contract and nothing depends on it.

### What still may not decide a category

Not the product name, the brand, the ingredient list, the nutrition panel, the
barcode prefix, an image, somebody's report, or an AI. If the source carries no
usable classification, the answer is that we do not have enough information.

There is no fuzzy matching, no edit distance, no embedding, no parent/child
equivalence and no hand-written taxonomy of our own. `breakfast cereals` is not
`cereal bars`; `potato crisps` is not `crackers`; `yogurts` is not `milk
drinks`. Broadening any of these would buy recall with a comparison we could
not defend. A row classified broadly and one classified specifically are not
comparable in either direction, even when one array is a prefix of the other.

The quotations above, their source URLs and the retrieval date live in
`backend/app/domains/off/taxonomy.py` and
`backend/tests/fixtures/off_payloads/SOURCES.md`.

And a category match means only what the source says it means: the same kind of
product. It does not mean the same nutrition, the same ingredients, the same
effect on any person, or the same safety. Those are different questions,
answered elsewhere, and the copy keeps them separate.

## A comparison needs a copy we can still vouch for

Open Food Facts contributors correct records and manufacturers reformulate
packs, so the repository already serves a cached copy for thirty days before
looking again. That window is defined once, in `app/domains/off/freshness.py`,
and both readers share it — two constants would eventually disagree.

The two readers use it differently, and the difference is the point:

* **The product lookup** uses it to decide whether to *try* a refresh, and is
  forgiving afterwards. When their API is slow, down, or the phone is offline, a
  stale copy is a better answer than a blank screen.
* **The comparison** uses it as a hard gate, on both sides. Showing a product and
  making a fresh comparative claim about two products are different acts. An
  expired copy of somebody else's category is not enough to support the second,
  so the alternative says "not enough information" while the Product Result
  above it carries on exactly as before.

An undated copy is not fresh. A record we cannot date is a record we cannot
vouch for, and treating unknown age as recent is the failure that gate exists to
prevent. Stale candidates are never refreshed over the network — the no-fan-out
rule is absolute — they simply do not qualify during that request.

## India availability is a statement about a database row

A candidate must carry the canonical Open Food Facts country id `en:india` in
its `countries_tags` array. Not a spelling in the raw `countries` text: their
own taxonomy already resolves "India", "Bharat", "Hindustan", "IN", "IND" and
every translation to that single id, so reading it is reading their answer
rather than re-deriving it badly from prose. A missing or unreadable country
array makes a candidate ineligible: absence is not availability.

It is never inferred from a barcode prefix, a brand, a language, an
FSSAI-looking name, or another shopper's scan. No retailer is consulted in this
milestone, so nothing may be described as in stock, nearby, or available now.
What the card can honestly say is that the source lists the product for India.

## Both products, one ruleset, one canonical truth

The current product and every candidate are evaluated against the **same
resolved production ruleset object**, in the same request. A letter produced
under one ruleset compared against a letter produced under another is not a
comparison at all, so the route passes the ruleset it already resolved rather
than resolving a second one.

Candidates are rebuilt from scratch every time. No stored grade is trusted — an
old letter may have been produced by rules that have since changed meaning.

A candidate must clear exactly the bar the current product cleared: a latest
snapshot marked `complete_for_grading`, an explicit `nutrition_basis`, a valid
published grade, and every required rule through its evidence lifecycle. When a
required rule is still a candidate constant, both products are ungradeable and
the honest answer is that we do not know.

**The card may never disagree with the screen it opens.** The candidate's grade,
decision and name come from the same canonical facts and the same helpers its
own Product Result uses — `latest_label_snapshots()` shares its definition of
"latest" with `latest_label_snapshot()`, and `result_identity()` is the one
place a product's published name and brand are decided. A catalogue row whose
numbers would grade A does not make a product an A: if the confirmed pack is a
D, no card offers it.

The snapshot read is **one query per page**, never one per candidate. Fifty
round trips inside a single Product Result is not a detail to fix later.

And "latest" means latest, not latest usable. A newer capture that could not
read the panel makes a candidate ineligible; reaching back to an older complete
version would answer with facts the pack may no longer have.

## Strictly higher, and never contradicting the card

A candidate qualifies only when its published grade is **strictly higher**.
Equal is not better — not for a shorter ingredient list, not for more protein,
not for a brand's reputation, not because shoppers like it. Same-grade
optimisation needs an explicit factor-comparison policy and there is not one
yet.

`NOT_GRADED` and `NOT_ENOUGH_INFORMATION` are never placed on the ladder. They
are states, not poor letters, and a cooking ingredient therefore never receives
a "better ghee".

A candidate must also not contradict the layer above it: its own canonical
decision has to be no worse than the current product's. A card headed "Better
option" whose own Product Result says SKIP against a BUY is a screen arguing
with itself.

Bases must match. A per 100 g panel is compared with a per 100 g panel and a
per 100 ml panel with a per 100 ml panel. There is no millilitre-to-gram
conversion, because it needs a density nobody printed.

## Selection is lexicographic. There is no score

Among eligible candidates: highest grade, then the better canonical action,
then the lowest barcode. Three comparisons, no arithmetic.

There is no `alternative_score`, no weighting and no composite number, and the
point is that one does not exist rather than that one is hidden from the API —
a structural test enforces it. The Constitution rejects any single composite
score averaging incompatible things, and a ranking key is exactly where one
would reappear.

Barcode is the final tie-break because it is stable, immutable and the one
identifier both stores already agree on. Selection is therefore independent of
insertion order, query-return order, shopper reports and who is asking.

## A name, or no recommendation

"Better option: 8901000000002" is not a suggestion anybody can act on. A barcode
is an identifier; a recommendation has to name something. A candidate whose
canonical facts carry no readable name is not offered, and the card fails closed
to the missing state if a malformed payload ever reaches it.

## Reading about a product you are not holding

Opening the alternative navigates to that product's ordinary Product Result with
`physical_pack_context=false` — a **reference view**.

The distinction it protects: the newest label snapshot for a barcode may be a
stranger's photograph of a stranger's packet. A shopper who merely tapped a
comparison card is not holding anything.

### The flag is a ceiling, not a grant

`physical_pack_context=true` is a **request**, and a request is not evidence. A
client sets it by default, an old build sets it always, and a hostile one sets
it deliberately — none of which tells the server anything about what is in
somebody's hand. Believing it, and then reading the newest snapshot for the
barcode as though it were the caller's own, attaches a stranger's lot and the
recall matched to it to a pack this device has never seen.

So authority is established from rows this server wrote, and the request can
only ever narrow it:

```
effective pack authority = requested  AND  server-proven
```

**Server-proven** means: this device's newest `ScanEvent` for this barcode is
itself a confirmed label capture. Ordered by server time (`created_at`, then
`id`) and never by the client's `scanned_at`, which an offline queue may
legitimately backdate and a hostile client may simply choose.

It never searches backwards. A newer plain scan of the same barcode means a
*different physical packet* is in this person's hand now, and its lot is unknown
until they photograph it. Reaching past that scan to an older capture would
attach last month's lot to today's packet, and would keep showing a shopper a
signal about a pack they put back on the shelf.

One resolver holds this rule — `backend/app/domains/product/pack_context.py` —
and the official-record layer, the community batch signal and the response flag
all read it. Two copies would drift, and the way that drift surfaces is a
stranger's recall on somebody's screen.

The response reports the authority that was actually in force rather than
echoing the request back, so no surface has to reconstruct the difference.

### What a reference view removes

A reference view **only ever removes authority**:

* **Official records.** No pack facts are passed to the matching layer at all, so
  no exact batch recall from somebody else's capture is shown. The envelope is
  still rendered and still says what it is.
* **Community.** Product-scoped observations are facts about the product and stay
  visible. Batch-scoped signals do not: a lot signal is about one packet and this
  viewer has none.
* **Reporting.** "Report what you saw" is a claim to have held this pack, so it is
  replaced — not shown and left to fail later — with an invitation to scan the
  product. Never with a synthetic scan.

The product science is unchanged, because that is a fact about the product
rather than about who is looking. And nothing is added: a device that really did
photograph the pack gets the ordinary Step 4 and Step 5 behaviour back, by its
own rules — and a device that did not gets no pack layer even when it asks for
one.

## What may not touch the selection

**The person.** No profile, no conditions, no medications, no family, no shelf,
no history, no preferences, no account age. The same pack plus the same cached
candidates plus the same ruleset produce the same alternative for everybody.
Personalised alternatives are a later paid layer; this one is free product
truth and reaches an anonymous device with no account at all.

**Shopper observations.** Community is an observation layer, not scientific
truth. It may not promote, demote, ban or endorse a candidate, and it may not
change a category or a grade.

**Official records.** A recall is matched to one physical pack, and we do not
know which batch of a candidate a shopper would encounter. So no product-level
reading of an official record ranks or rejects a candidate, and nothing is ever
described as recall-free or regulator-approved.

**Money.** Price, MRP, retailer stock, value for money and rupees per 100 g are
Step 6B, with their own provenance. The alternative here is chosen
independently of what anything costs.

**AI.** Nothing in this path reaches the gateway. Category, availability,
eligibility and selection are deterministic, and the customer copy is keyed.

Each of those exclusions is enforced structurally — by an import guard, an
invariance test, or both — rather than by remembering not to call something.

## Discovery is bounded, paged, and reads only the cache

Candidates come from products already in Store A. No Open Food Facts search
endpoint is called, no category is crawled, and there is never a request per
candidate.

### Every gate Store A can answer runs before the row limit

The canonical category, the canonical India tag, the exclusion of the shopper's
own barcode, and the age of the copy are all SQL predicates. When those gates
run in Python *after* a `LIMIT`, a stale row, a foreign row or a row from
another category consumes a place in the window and is then discarded — so a
window named fifty silently yields far fewer, and the rows it displaced are
never reached at all.

### Paged, because a single window could starve permanently

Store A knows which products are the same kind and sold here. Only Store B knows
which of them anybody has ever confirmed a label for, and the licence wall
forbids joining the two in the database. A single window therefore had a
permanent blind spot: if the first fifty source-qualified rows all lacked a
usable snapshot, the fifty-first — a perfectly good candidate — could never be
reached, on that request or any future one, because the window always started in
the same place. Nothing about it was random, so no amount of re-scanning,
waiting or refreshing would have changed it.

Discovery therefore pages: `DISCOVERY_PAGE_SIZE` qualifying rows at a time, up
to `MAX_DISCOVERY_PAGES`, walking down the barcode order with a keyset cursor
and costing one batched Store B read per page. It stops early when the running
winner is at the top of both ladders, because barcode is the final tie-break and
ascends as the scan proceeds, so nothing further down could displace it.

### Running out of budget is not the same as running out of products

Two distinct reason codes, because they are two different facts:

| Reason | What it means |
| --- | --- |
| `no_comparable_candidate_in_cached_data` | We reached the end of the qualifying rows and found nothing. A fact about the cached data. |
| `search_budget_exhausted_before_a_comparable_candidate` | We stopped looking first. A fact about our own work limit. |

Both render as the same careful sentence to the customer — the distinction is an
engineering signal, never a claim. Only the first means the data has been
exhausted; the second is what says the budget needs raising.

Because the cache is not the Indian market, an empty result means we cannot
establish a comparable alternative. It does not mean none exists, and no
surface may render it as though it did.

## Coverage is measured; shopping is not

Every failure path returns the same sentence, which is right for the customer
and blind for us. So each request records **which gate closed**, from a closed
set, with counts — `backend/app/domains/alternatives/observability.py`.

It records no account id, device id, barcode, product name, brand, batch number,
FSSAI licence, raw category or country text, label facts, ingredients or
nutrition values. Not hashed, not truncated. The question being answered is "how
often does the category gate close", which is an engineering fact about our own
cached data. A record of which products a person scanned is a different thing
entirely, and is not built here.

A counter may not change an answer: every function returns `None` and swallows
its own failures, so the Product Result is byte-identical whether observability
is working, broken or absent.

## The ODbL wall holds

The candidate's category and country are Open Food Facts fields. They are read
from Store A at query time, paired with facts of ours in memory for the length
of one response, and thrown away with it. Its name, brand and panel are not
theirs at all: they come from a photograph somebody took of the pack, which is
independently sourced and creates no derived database — the same reasoning that
makes the confirmed-label table itself sound.

Nothing is persisted. Step 6A introduced no table and no migration, and it
writes nothing to either store. In particular there is no cached
`canonical_category`, no `availability` column and no table of chosen
alternatives — a stored alternative is a join between the two stores written
down, which is the derived database ODbL's share-alike clause attaches to.

Attribution travels with the data: the candidate card renders the same verbatim
notice as every other surface showing their fields, using the same component.
The category is described as theirs, never as a GlamGenius-certified or
official category, because we read it rather than authored it.

`backend/tests/test_odbl_data_wall.py` carries the Step 6A additions. If one
fails, the change is about to put the product under ODbL.

## The words

Never *healthier*, *safer*, *cleaner*, *best*, *superior*, *junk* or *toxic*.
The comparison is between two grades and the card says exactly that: "Grade B
instead of Grade C. Same category."

Never *no alternatives found*, *this is the best*, or *nothing better exists*.
The honest missing line is about what we know: "Not enough information to
suggest a comparable alternative yet."

The heading is **Better option** — neutral, and not a swap, an upgrade or a
smart choice.

The card sits below every evidence layer and below the shopper observations,
immediately above the closing actions. It carries no price, no cart, no
affiliate link, no stars, no review count and nothing addressed to the person
holding the phone. Tapping it is ordinary navigation to that product's own
Product Result: reading about a pack is not scanning one, so no scan event is
recorded and the physical-pack layers keep failing closed on it.

## Where it lives

| Piece | File |
| --- | --- |
| What makes two products comparable | `backend/app/domains/alternatives/category.py` |
| What the source's category and country fields actually mean | `backend/app/domains/off/taxonomy.py` |
| Server-proven physical-pack authority | `backend/app/domains/product/pack_context.py` |
| Coverage counters, with no identifier in them | `backend/app/domains/alternatives/observability.py` |
| Eligibility, ranking, the basis gate, the policy version | `backend/app/domains/alternatives/policy.py` |
| Discovery, evaluation, the response envelope | `backend/app/domains/alternatives/service.py` |
| The one freshness window | `backend/app/domains/off/freshness.py` |
| Canonical identity and the batch snapshot read | `backend/app/domains/product/service.py` |
| Where it joins the Product Result, and reference mode | `backend/app/api/v2/product.py`, `read_product_verdict` |
| The card | `frontend/src/components/verdict/BetterOption.tsx` |
| Every word it says | `frontend/src/strings/verdict.ts`, `S.betterOption`, `S.referenceView` |
| Store A's schema, its discovery index, and its evolution | `backend/app/domains/off/models.py`, `backend/app/domains/off/store.py` |
| Backend tests | `backend/tests/test_step6a_comparable_alternative.py` |
| Taxonomy, provenance, budget and index tests | `backend/tests/test_step6a1_discovery_provenance.py` |
| Frozen Open Food Facts payloads, and where they came from | `backend/tests/fixtures/off_payloads/` |
| Licence tests | `backend/tests/test_odbl_data_wall.py` |
