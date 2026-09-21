# Step 12A — the confirmed-label change authority

## The question this layer answers

**What did two confirmed physical-pack observations of the same product say
differently?**

That is the whole job. The answer is a *fact*, and Step 12A is finished the
moment it has been stated. Whether the change matters, whether it is good or
bad, whether a rule now applies and whether anybody should be told are later
questions, and nothing in this layer is allowed to start answering them.

Separating the fact from the reaction is not tidiness. A product that could
tell somebody their moisturiser was "reformulated" needs first to be able to
prove that two people photographed two different packs — and to be unable to
say it when all that really happened was that a catalogue was edited, a camera
read a line break differently, or a reviewer published a synonym.

## Two authorities, not one: knowing and publishing

Step 12A holds **two** separate authorities, and the post-merge correction
exists because the first was mistaken for the second.

| | **Internal deterministic change authority** | **Customer-publishable change claim** |
| --- | --- | --- |
| Question | What do these two stored observations say differently? | May a customer be told that? |
| Decided by | `project_label_change()` in `change_projection.py` | `comparison_is_publishable()` in `label_evidence.py` |
| Inputs | Two `LabelSnapshot` rows and nothing else | Whether each of those observations has a source the customer could open |
| Wrong answer looks like | A change fact that does not follow from the rows | A true fact stated in the app's own voice, with nothing to point at |
| If it cannot answer | `LabelHistoryInvariantError` — refuse in the open | Withhold the claim; the page carries on |

The engine is not weakened and is not going away: it decides identity, it
decides what the migration must move, and it is what the integrity check runs.
`test_3*` still proves it against the same fixtures it always did, by calling
it directly.

What changed is that its output is no longer published just because it exists.
The Product Constitution is unconditional — *the app never makes a claim in its
own voice; it reports what a named, openable source says. No source, no claim.*
"This moisturiser's ingredient list changed, and here is the ingredient that
appeared" is a claim about a manufacturer. Two of our own database rows are not
a source for it.

### Identity metadata is not evidence

These are the four things closest to hand, and not one of them may be offered
as the source of a change claim:

| Value | What it actually is |
| --- | --- |
| `version_number` | Our own counter. It says how many times *we* recorded something, not what any pack said. |
| `content_fingerprint` | Our own integrity hash. It detects that stored bytes moved; it is not a witness to a pack. |
| `observed_at` | When we wrote a row. A timestamp of our bookkeeping. |
| The stored transcription | The thing the claim is *derived from*. A claim cannot be its own independent support. |

All four are **identity and integrity metadata**. They are exactly why the
engine can be trusted internally, and exactly why they cannot travel outward as
evidence: pointing at any of them is the app citing itself.

### Why the answer is currently "no", every time

Walk the chain a confirmed observation is built from.
`POST /scan/label/transcribe` reads a private `MediaAsset` and records an
`AIRun` with an `AIRunOutput`. `POST /scan/label/confirm` writes a `ScanEvent`
carrying the `ai_run_id`, and a `LabelSnapshot` carrying the `scan_event_id`.

Not one of `AIRun`, `AIRunOutput`, `ScanEvent` or `LabelSnapshot` persists the
`media_asset_id`. The photograph is reachable only from the request that
created the run, and that request is gone. Even if it were stored, every media
route is account-private (`app/domains/media/service.py::get_owned_asset`) — so
the photograph behind a stranger's observation is not something this caller may
open, and the Product Result answers an **anonymous device token**.

So `label_evidence.observation_source()` looks for an openable locator, finds
that the schema has nowhere to keep one (`_LOCATOR_FIELDS` is empty, and says
in the code why), and returns `None`. `comparison_is_publishable()` therefore
returns `False` for every pair, and the Product Result withholds the claim.

This is not a permanent verdict, and it is not relaxable from here. Restoring
publication is a **schema** change — persisting a locator on the
confirmed-observation chain and giving it a lawful public reader — reviewed on
its own terms. `is_openable_customer_source()` refuses anything that is not
`http`/`https` with a host, and refuses any path under `/api/`, `/media/`,
`/scan/` or `/internal/`, so a locator that merely points back into this
application cannot be used to switch publication on.

### Withheld is not "unchanged", and not "never seen"

There are three distinct outcomes and they must stay distinct:

| `label_change` | Means |
| --- | --- |
| `null` | This product has no confirmed observation at all. Nobody has photographed it. |
| `status: "unavailable"` | There is history, and we are not stating what it says — either the stored history failed its invariants, or the comparison has no openable source. |
| `status: "first_observed_version"` / `"changed"` | A publishable claim, with its sources. |

`unavailable` is the governed vocabulary that already existed for the
fail-soft case (`UNAVAILABLE_PROJECTION`), and the publication boundary reuses
it rather than inventing a second way to say nothing. When it is returned,
**none** of `changed_fields`, the formula delta, the ingredient names or
`first_observed_version` appears anywhere in the response.

That last part matters because the envelope is not the only door.
`label_version.changed_fields` carries the same claim in a smaller box, so it
is `null` when the comparison is unpublishable. Explicitly **not** `[]` — an
empty list asserts that nothing changed, which is a claim in its own right and
one we have no source for either. `LabelSnapshot.changed_fields` is still
stored, still migrated, still what the integrity check compares against; it
simply stops being served.

## Store B is the only authority

A `LabelSnapshot` exists because somebody photographed a physical pack and
confirmed what it said. Nothing else creates one.

Open Food Facts refreshing its copy of a product is a catalogue edit, not an
observation of a pack, and it can never produce a version here. The two stores
still meet only in memory, at query time, on barcode — see
[`ODBL_DATA_WALL.md`](ODBL_DATA_WALL.md). A product with an Open Food Facts
record and no confirmed observation returns `label_version: null` and
`label_change: null`.

## What counts as a different label

This is where Step 12A had to correct something, and it is worth stating
plainly because it looks like a detail and is not.

Label version identity canonicalises *presentation*: a doubled space, a
trailing space, whether the pack printed a carriage return or a line feed.
Two photographs of one pack must not become two versions of it, or a product's
history fills up with changes nobody made.

But a **line break at the top level of an ingredient list is not presentation.**
Step 7B deliberately refuses to guess where an entry ends when it sees one, and
reports `ambiguous_boundary` with no ingredients at all rather than a list it
invented. So `"Water\nGlycerin"` and `"Water Glycerin"` are two labels the
product reads completely differently — one yields nothing, the other yields a
single entry called "Water Glycerin".

The version authority previously folded those together. That meant a pack whose
list the parser could not read could be stored as "no change" against a pack
whose list it could, and the earlier reading would stay attached to the later
observation. So the rule is:

> **The version authority preserves exactly those boundary distinctions the
> Step 7B structural grammar marks as significant, and folds away the rest.**

"Significant" is the grammar's word, not ours. Step 7B refuses a line boundary
only **outside balanced grouping**; inside grouping, *grouping still wins* — the
run is kept inside one entry, and Step 7A collapses it when producing that
entry's canonical key. So:

| Pair | Same version? | Why |
| --- | --- | --- |
| `Water\nGlycerin` vs `Water Glycerin` | **No** | Top-level break. One reads as `ambiguous_boundary` with no entries, the other as one entry. |
| `Parfum (A\nB), Water` vs `Parfum (A B), Water` | **Yes** | Protected by grouping. Same entries, same canonical identities. |
| `Parfum（A\nB）, Water` vs `Parfum（A B）, Water` | **Yes** | Fullwidth brackets group too — Step 7B reads structure through an NFKC view. |
| `Water\r\nGlycerin` vs `Water\nGlycerin` | **Yes** | Which boundary character was printed is presentation. |
| `Water)G\nH` vs `Water)G H` | **No** | Grouping never balances, so the grammar declines to speak and every boundary is kept. |

Treating a protected wrap as a new version would have been its own defect: the
label version identity is what decision memory (`ScanDecisionEvent`) and shelf
links (`InventoryProductLink`) are pinned to, so splitting it over a line wrap
detaches a remembered decision from the pack it was made about.

Concretely, in `app/domains/product/service.py`:

- Fields the parser reads (`FORMULA_SIGNIFICANT_FACT_FIELDS`, today just
  `ingredients_text`) collapse whitespace **except** that a run carrying a
  boundary at a position the grammar marks significant becomes a single `\n`.
  A break at either end counts, because Step 7B treats a leading or trailing
  break exactly as it treats one in the middle.
- Every other field collapses whitespace as before.

Neither the boundary set nor the grammar is restated on the product side.
`LINE_BOUNDARIES` and `boundary_significance()` come from the parser through
`formula_projection.py`, the product domain's one door into formulas. A second
copy of the grouping and Unicode rules would answer the fullwidth-bracket case
differently on the day somebody forgot it, and the version identity would move
underneath every remembered decision. `test_4g` fails if the product domain
grows a grammar of its own.

### The properties this rests on

Measured, not asserted (see the PR for the exact runs):

- **Faithfulness** — canonicalising never changes what the parser concludes.
  120,000 samples, 0 violations. This is why the canonical form is a legitimate
  stand-in for the printed one.
- **Refinement** — two labels equal under the new rule were equal under the old
  one. 108,432 whitespace-variant pairs, 0 violations; strictly finer in 89,668.
  It can only split identities, never merge them, which is what makes the
  backfill safe.
- **Governed distinction** — every one of those 89,668 distinctions rests on a
  boundary the grammar refuses at that position, or on the grammar declining to
  speak for the text at all. 0 ungoverned.
- **Protected folds merge** — 40,000 generated labels with a boundary inside
  balanced, nested and compatibility-form grouping: all 40,000 fold to one
  version.

## Backward compatibility

`migrations/versions/j8k9l0m1n2_step12a_boundary_aware_label_identity.py`
changes no schema. It re-derives the two values that depend on the rule:

- **`content_fingerprint`** — a pure function of one row's own immutable facts.
  There is exactly one correct value and no pairing decision. It moves under
  the source rule below, and the copies in `scan_decision_events` and
  `inventory_product_links` are realigned **by `label_snapshot_id`**, never by
  fingerprint value, and only for the snapshot ids this invocation actually
  moved.
- **`changed_fields`** — a statement about *two* rows, and therefore equally
  stale after the rule moves. A pack whose ingredient line wrapped differently
  was legitimately recorded as "only the quantity changed", because under the
  old rule the newline *was* a space:

  ```text
  v1  ingredients_text = "Water\nGlycerin"   net_quantity = "100 g"
  v2  ingredients_text = "Water Glycerin"    net_quantity = "120 g"

  stored (old rule)  ["net_quantity"]
  new rule           ["ingredients", "net_quantity"]
  ```

  Left behind, the projection would recompute the second value on the very next
  request and refuse the whole history as corrupt — for data nothing was ever
  wrong with. So the migration moves it, in both directions.

The predecessor is always the row's explicit `previous_snapshot_id`. Never the
previous version number, never the same barcode, never a matching fingerprint:
pairing rows by anything other than the link they carry would invent a history.

### The source-fingerprint rule

A migration that moves an identity rule is not entitled to assume every stored
row was written under the rule it is moving *from*. Some were not — a row whose
fingerprint never described its own facts is corrupt, and it is corrupt for a
reason nobody here can see.

Recomputing such a row unconditionally does something worse than leaving it
broken. It replaces a value that is visibly wrong with one that is *plausibly
right*: the row now passes the new integrity check, the corruption is gone from
the record, and nothing downstream will ever ask about it again. That is
laundering, and it is irreversible.

So the rule, in both directions:

> **A row may migrate only if its stored `content_fingerprint` is exactly the
> value the *source* rule derives from that row's own facts.**

"Source rule" is the rule the migration is leaving: the old, non-boundary-aware
composition on `upgrade()`, the new boundary-aware one on `downgrade()`. Rows
that fail this are left exactly as found, for the Step 12A integrity check to
refuse in the open. So are rows whose `facts` is not a fact object at all.

Three consequences follow, and each is a test:

1. **Predecessors must be source-valid too.** `changed_fields` is a statement
   about two rows. A difference recomputed against a predecessor whose own
   identity was never trustworthy is not a migrated value, it is a new
   invention. Such rows are skipped.
2. **A row already inconsistent with its own history is not ours to fix.**
   Where the stored `changed_fields` is not what the source rule derives from
   the pair, it is left alone for the same reason.
3. **Linked copies follow only migrated snapshots, named one by one.** The
   `UPDATE … FROM product_label_snapshots` statements in
   `scan_decision_events` and `inventory_product_links` are parameterised by
   the exact snapshot ids this invocation moved. A blanket "realign every copy
   that disagrees with its snapshot" would quietly repair copies of rows the
   migration deliberately refused to touch — the same laundering, one join
   further out.

#### Operational note: already-executed migrations

This rule can only govern a database that has **not yet** run
`j8k9l0m1n2`. Where the revision has already been applied to a persistent
database, the old unconditional pass has already rewritten whatever it
rewrote, and the pre-migration source state no longer exists to be checked
against. Nothing in this correction repairs that, and nothing in it should be
read as claiming it does: recovering such a database is an operational
question — restore the pre-migration state, or accept the rows as they now
stand and let the integrity check speak — not a code question.

The migration carries a frozen copy of the rule rather than importing it — a
migration has to keep doing what it did on the day it ran — and that copy now
includes the part of the Step 7B structural grammar the rule depends on, so it
folds a protected wrap exactly as the application does.
`test_29_the_boundary_rule_is_one_rule_in_three_places_that_agree` asserts the
copy still agrees with the application across grouping, nesting and
compatibility forms, and across the derived difference between every pair of
those texts, so the duplication is visible rather than silent.

Rejected alternatives, and why:

| Option | Why not |
| --- | --- |
| A `fingerprint_version` column | Schema churn, and it branches the identity authority forever. |
| Accept either fingerprint in the integrity check | Weakens the check permanently to avoid a one-off backfill. |
| Leave version identity coarse | The line-broken observation is never stored as a version at all, which is the defect. |

## The comparison contract

```python
project_label_change(*, current: LabelSnapshot, previous: LabelSnapshot | None)
    -> LabelChangeProjection
```

Pure. No session parameter, no query, no write, no clock, no model — and no
"latest" lookup. Selection belongs to the caller; this function compares
exactly the two observations it was handed, and the same pair yields the same
answer on any machine on any day.

### Independence from the identity registry

The comparison keys each printed entry with Step 7A's canonical normalizer and
**does not resolve it to a substance**. `canonical_formula_entries` in
`app/domains/formulas/service.py` is `resolve_formula` with the registry lookup
removed.

That is deliberate. The registry is a table a reviewer keeps adding to. A
comparison that consulted it would report a formula change on the day somebody
published a synonym — a claim about a manufacturer that nobody made and no
label supports.

### The product domain's one door

Everything the product domain takes from the formula engine arrives through
`app/domains/product/formula_projection.py`, and no other file under
`app/domains/product` imports `app.domains.formulas`. The product domain never
reaches the substances domain at all. Both boundaries are held up by name in
`tests/test_step7b_formula_resolution.py` and
`tests/test_step7a_substance_identity.py`.

## History invariants, and why none of them is repaired

The projection refuses, with `LabelHistoryInvariantError`, when:

| Invariant | Broken shape |
| --- | --- |
| `*_label_facts_invalid` | Stored `facts` is not an object — a JSONB array, string or number, or an object the canonicaliser cannot read. |
| `*_label_fingerprint_mismatch` | A stored fingerprint no longer describes the stored facts. |
| `*_label_version_invalid` | A version number below 1. |
| `first_label_version_has_predecessor` | Version 1 names one. |
| `first_label_version_has_changed_fields` | Version 1 claims a difference from nothing. |
| `label_predecessor_missing` | Version > 1 has none. |
| `label_predecessor_identity_mismatch` | The supplied predecessor is not the stored one. |
| `label_predecessor_barcode_mismatch` | Two different products. |
| `label_version_chain_non_contiguous` | A gap in the chain. |
| `adjacent_label_versions_have_same_content` | Two adjacent versions with identical canonical content. |
| `label_changed_fields_mismatch` | Stored `changed_fields` disagrees with the deterministic recomputation. |

No supported write path produces any of these. A projection that picked
whichever reading still parsed would publish a change fact nobody observed and
hide the corruption for good, so each one refuses instead.

The reason string is for the server log. `AppError.to_detail()` emits only the
code, the message and `retryable`, and this error carries no `extra`, so no
invariant name and no internal identifier can reach a customer.

## On the Product Result

`GET /api/v2/scan/verdict/{barcode}` gains exactly one key, `label_change`.
Its **publishable** shape — which no caller receives today, for the reason in
*Two authorities* above — is:

```json
{
  "scope": "confirmed_label_history",
  "status": "first_observed_version" | "changed" | "unavailable",
  "current_version": 2,
  "previous_version": 1,
  "changed_fields": ["ingredients"],
  "formula": {
    "status": "not_applicable" | "unchanged" | "reordered_only"
             | "ingredient_set_changed" | "not_comparable",
    "previous_parse_status": "parsed",
    "current_parse_status": "parsed",
    "only_on_current_label": [{"name": "Niacinamide", "occurrences": 1}],
    "only_on_previous_label": [{"name": "Glycerin", "occurrences": 1}]
  }
}
```

What a caller receives while no observation carries an openable source is the
governed unavailable envelope, and `label_version.changed_fields` is `null`
beside it:

```json
{
  "label_version": {
    "id": "…", "version_number": 2, "content_fingerprint": "…",
    "observed_at": "…", "changed_fields": null, "completeness": "…"
  },
  "label_change": {"scope": "confirmed_label_history", "status": "unavailable"}
}
```

`scope` is in the envelope because this is the **product's** confirmed label
history, not a statement about the packet in the caller's hand. It is shown in
reference mode for the same reason, and no surface may render it as "your
packet changed" unless the server has separately proved which pack this device
is holding.

The envelope is additive and nothing else moves. A test compares the whole
response across a label change and names the keys that differed —
`ingredients`, `label_version`, `label_change` — so a future change that let
this envelope disturb the grade, the band, the evidence, an alternative or the
value comparison fails without anybody having to remember to assert it.

**An addition may not take the page down with it.** When stored history fails
its invariants, the projection still refuses; the route logs the reason and
serves `status: "unavailable"`. The grade, the band and every card around it
were established without Step 12A and are still true. `unavailable` is
deliberately distinct from `null`: `null` means this product has never been
observed, and conflating the two would hide the corruption the invariant just
caught.

Failing soft only works over a failure the route can *recognise*. `facts` is
JSONB and the column can hold an array, a bare string or a number; the
canonicaliser is entitled to assume an object, so the shape is checked at the
Step 12A integrity boundary and becomes `*_label_facts_invalid` rather than an
`AttributeError` three frames down. `label_content_fingerprint` keeps its own
contract unweakened.

A snapshot whose facts cannot be read is also not something the *verdict* can be
built from. The route still reports the version — decision memory and shelf
links are pinned to its id and fingerprint, and hiding it would detach them —
but grades from what can still be read, exactly as it does for a product nobody
has photographed, and says so in `facts_provenance`. `test_35` corrupts a real
row in PostgreSQL and asserts the resulting page is identical, key for key, to
the page that product gets with no confirmed observation at all.

### One boundary, asked everywhere

"Can these stored facts be read as a fact object?" is asked by the Product
Result, by the comparable-alternative engine, and by both FSSAI complaint
routes. It is therefore **one** function, in the product domain's service:

```python
readable_label_snapshot(snapshot) -> LabelSnapshot | None
readable_label_facts(snapshot)    -> dict[str, Any]
```

It is deliberately the *weakest* question in this area, and must not be
confused with the strong one. `project_label_change()` asks whether a whole
stored history is coherent and refuses loudly when it is not. This asks only
whether one row's `facts` is a mapping, and answers `None` / `{}` when it is
not. A caller that needs the strong guarantee must still ask for it.

Two consequences the post-merge correction had to close:

- **The alternative engine gets the same authority.** It used to receive the
  original `snapshot` while the page around it had already been built from the
  validated one, and `current_facts.get(...)` then raised on a JSONB array
  three frames down. It now receives `readable_label_snapshot(snapshot)` —
  `None` when the row is unreadable, which is the shape it already handles for
  a product with no confirmed observation. There is no alternative-specific,
  weaker version of the rule.
- **Both FSSAI paths get it too.** `POST /api/v2/reports/fssai/preview` and
  `/confirm` read pack facts directly. Unreadable facts now mean *pack fields
  are unavailable* — never a silent substitution of Open Food Facts values,
  which are a catalogue and not the pack a complaint is about. Preview answers
  with the governed "what is missing" shape instead of a 500; confirm returns
  the **existing** `422 pack_fields_missing` and creates no
  `FssaiComplaintHandoff` row. No product name, brand, batch or licence number
  is ever invented to fill a gap.

### Confidence describes the facts actually used

`confidence` and `facts_provenance` are two statements about the same thing and
must not be able to disagree. They are therefore decided together, in one
branch:

| Facts used to build and grade the response | `facts_provenance` | `confidence` |
| --- | --- | --- |
| A readable confirmed snapshot | `confirmed_label_snapshot` | that snapshot's own confidence |
| Open Food Facts, because no readable snapshot | `open_food_facts` | `unverified` |
| Nothing usable at all | `open_food_facts` | `not_enough_information` |

The defect this closes: when a snapshot's facts were unreadable the route fell
back to the Open Food Facts record for the *facts* but kept the product
record's stored confidence for the *label*, so a page built entirely from a
catalogue could be badged "Checked by us against the pack." Confidence is a
claim about the facts in front of the customer, not about a row that exists
somewhere. The vocabulary is unchanged — these are the existing
`ProductConfidence` values, no new level was added.

## The formula classification

`changed_fields` decides whether the ingredient text moved at all. If it did
not, the answer is `unchanged` — **even when neither list can be parsed.** Two
packs that printed the same unreadable list did not change, and answering
`not_comparable` there would invite a client to tell somebody the label moved
when it demonstrably did not.

When it did move:

| Status | Meaning |
| --- | --- |
| `unchanged` | The keyed entry sequence is identical (a case-only or spacing-only edit). |
| `reordered_only` | The same entries the same number of times, in a different order. |
| `ingredient_set_changed` | Occurrence counts differ. `only_on_current_label` / `only_on_previous_label` report the delta in printed order, in the words the pack used. |
| `not_comparable` | One of the lists could not be read as a list, or an entry has no usable canonical key. No partial difference is calculated. |

Both parse statuses are always reported, so a client can tell "we could not
tell where one ingredient ended" from "this is not a list".

### Why the two sides are not called "added" and "removed"

They are named for where an entry was *seen*, not for what somebody did. Two
photographs cannot establish that a manufacturer did anything: one pack printed
one list and a later pack printed another. Stating the observation is what the
first of the six writing rules in [`LEGAL_RULES.md`](../../LEGAL_RULES.md) asks
for, and it also keeps this envelope out of the way of the official-record
notice that may be sitting on the same screen, where that vocabulary means
something else entirely and means it about safety.

Duplicates are counted, not flattened. `Water, Glycerin, Glycerin` becoming
`Water, Glycerin, Niacinamide, Niacinamide` reports Niacinamide twice on the
current label and Glycerin once on the previous one. Comparing sets would
report a single difference in one direction, which is not what the two labels
say.

**Printed order is printed order.** A reorder is not a concentration claim.
Ordering conventions do exist in some regimes, and reading one here would be an
unsourced regulatory inference — see
[`FORMULA_RESOLUTION.md`](FORMULA_RESOLUTION.md).

## The first observation

Never seen before is not a change. It is `first_observed_version`, with
`formula.status = not_applicable`, no `changed_fields`, and no `previous_version`.
Nothing was added, nothing was removed, and no pack was reformulated — we had
simply never seen this one.

## Deliberately out of scope

Step 12A does not, and its code contains no field one could be smuggled into:

- infer why a manufacturer changed a pack;
- infer concentration from printed order;
- decide whether a change is good, bad, safer, stronger or weaker;
- interpret an FSSAI notice or any other regulatory instrument;
- subscribe anybody to a product, send a notification, or run a watcher;
- publish a change claim without an openable source for each observation;
- store a change event, a watch, a queue or a schedule — the delta is derived
  on read from history that already exists;
- add paid infrastructure of any kind.

None of those exists in this repository. Later roadmap steps are named in the
roadmap, not here; nothing in this layer should be read as a statement that the
work behind one of them has been started.
