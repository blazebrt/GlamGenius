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

## The chain, in order

```
current graded product
  -> conservative category leaf, from the Open Food Facts row, at runtime
  -> bounded read of cached Store A candidates
  -> the same adapter, the same grader, the same resolved ruleset
  -> strictly higher grade, no worse a decision, the same panel basis
  -> lexicographic selection: grade, then action, then barcode
  -> at most ONE candidate on the public contract
```

`backend/app/domains/alternatives/` owns it: `category.py` reads the source
taxonomy, `policy.py` holds the eligibility and ranking rules, `service.py`
runs the chain. The domain consumes the grading, Open Food Facts and
presentation layers; it owns none of them.

## Category comparability is not a scientific claim

Two products are comparable when the **final** token of each one's Open Food
Facts category path, normalised the same way, is the identical string.

The normalisation is deliberately dull: NFKC, split on the source's comma,
trim, collapse repeated whitespace, casefold, drop empties, take the last
surviving token. `"Foods, Breakfasts, Breakfast cereals"` and `"Plant foods,
Breakfast cereals"` both resolve to `breakfast cereals`.

Nothing else is allowed to decide a category. Not the product name, the brand,
the ingredient list, the nutrition panel, the barcode prefix, an image,
somebody's report, or an AI. If the source carries no usable category, the
answer is that we do not have enough information.

There is no fuzzy matching, no edit distance, no embedding, no parent/child
equivalence and no hand-written taxonomy of our own. `breakfast cereals` is not
`cereal bars`; `potato crisps` is not `crackers`; `yogurts` is not `milk
drinks`. Broadening any of these would buy recall with a comparison we could
not defend.

And a category match means only what the source says it means: the same kind of
product. It does not mean the same nutrition, the same ingredients, the same
effect on any person, or the same safety. Those are different questions,
answered elsewhere, and the copy keeps them separate.

## India availability is a statement about a database row

A candidate must carry `India` (or the source's own `en:india` tag form) as an
exact normalised token in the Open Food Facts `countries` field. A missing
country list makes a candidate ineligible: absence is not availability.

It is never inferred from a barcode prefix, a brand, a language, an
FSSAI-looking name, or another shopper's scan. No retailer is consulted in this
milestone, so nothing may be described as in stock, nearby, or available now.
What the card can honestly say is that the source lists the product for India.

## Both products, one ruleset

The current product and every candidate are evaluated against the **same
resolved production ruleset object**, in the same request. A letter produced
under one ruleset compared against a letter produced under another is not a
comparison at all, so the route passes the ruleset it already resolved rather
than resolving a second one.

Candidates are rebuilt from scratch every time. No stored grade is trusted — an
old letter may have been produced by rules that have since changed meaning.

A candidate must clear exactly the bar the current product cleared: enough
label facts, a valid published grade, and every required rule through its
evidence lifecycle. When a required rule is still a candidate constant, both
products are ungradeable and the honest answer is that we do not know.

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

## Discovery is bounded and reads only the cache

Candidates come from products already in Store A. No Open Food Facts search
endpoint is called, no category is crawled, and there is never a request per
candidate. One bounded query, ordered by barcode, capped by
`MAX_DISCOVERY_CANDIDATES`.

A coarse SQL filter prunes the scan and every row it returns is re-tested with
the exact parser in Python, so the filter can only ever cost recall — it cannot
admit a product from another category. Wildcards in a source category are
escaped, so a category is data and never a pattern.

Because the cache is not the Indian market, an empty result means we cannot
establish a comparable alternative. It does not mean none exists, and no
surface may render it as though it did.

## The ODbL wall holds

The candidate's name, brand, category and country are Open Food Facts fields.
They are read from Store A at query time, paired with a grade of ours in memory
for the length of one response, and thrown away with it.

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
| Category leaf, country tokens, the coarse filter | `backend/app/domains/alternatives/category.py` |
| Eligibility, ranking, the policy version | `backend/app/domains/alternatives/policy.py` |
| Discovery, evaluation, the response envelope | `backend/app/domains/alternatives/service.py` |
| Where it joins the Product Result | `backend/app/api/v2/product.py`, `read_product_verdict` |
| The card | `frontend/src/components/verdict/BetterOption.tsx` |
| Every word it says | `frontend/src/strings/verdict.ts`, `S.betterOption` |
| Backend tests | `backend/tests/test_step6a_comparable_alternative.py` |
| Licence tests | `backend/tests/test_odbl_data_wall.py` |
