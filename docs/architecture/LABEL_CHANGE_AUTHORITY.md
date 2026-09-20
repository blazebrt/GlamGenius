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
observation. So the rule is now:

> **The version authority preserves every distinction that can change what the
> formula parser concludes, and folds away the rest.**

Concretely, in `app/domains/product/service.py`:

- Fields the parser reads (`FORMULA_SIGNIFICANT_FACT_FIELDS`, today just
  `ingredients_text`) collapse whitespace **except** that any run containing a
  boundary character becomes a single `\n`. Which boundary was printed is
  presentation; *that* one was printed is content. A break at either end counts,
  because Step 7B treats a leading or trailing break exactly as it treats one in
  the middle.
- Every other field collapses whitespace as before.

The boundary set is not restated. `LINE_BOUNDARIES` is exported from the parser
itself, because a second copy would be a second answer to "where can a boundary
be" and the two would drift.

This rule is strictly **finer** than the one it replaces: two labels that are
the same under the new rule were always the same under the old one. It can
therefore only split identities, never merge them — which is what makes the
backfill safe.

## Backward compatibility

`migrations/versions/j8k9l0m1n2_step12a_boundary_aware_label_identity.py`
recomputes `product_label_snapshots.content_fingerprint` from the immutable
`facts`, then realigns the copies in `scan_decision_events` and
`inventory_product_links` **by `label_snapshot_id`**, never by fingerprint
value. It changes no schema. Only rows whose `ingredients_text` contains a
boundary character move at all.

The migration carries a frozen copy of the rule rather than importing it: a
migration has to keep doing what it did on the day it ran. A test
(`test_29_the_boundary_rule_is_one_rule_in_three_places_that_agree`) asserts the
copy still agrees with the application, so the duplication is visible rather
than silent.

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

`GET /api/v2/scan/verdict/{barcode}` gains exactly one key, `label_change`:

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
- interpret an FSSAI notice or any other regulatory instrument (**Step 12B**);
- subscribe anybody to a product, send a notification, or run a watcher
  (**Step 12C**);
- store a change event, a watch, a queue or a schedule — the delta is derived
  on read from history that already exists;
- add paid infrastructure of any kind.
