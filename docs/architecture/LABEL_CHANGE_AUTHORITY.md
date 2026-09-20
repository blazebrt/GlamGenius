# Step 12A — Formula / Pack Change Authority

## Purpose

Step 12 begins by answering a narrow factual question before GlamGenius ever
tries to watch, notify, or interpret a regulatory change:

**What did two confirmed physical-label versions say differently?**

The authority is Store B's immutable LabelSnapshot history. Open Food Facts can
refresh catalogue data, but it does not create a confirmed physical-label
version and therefore cannot manufacture a Step 12A change.

## Inputs

The comparison receives two explicit snapshots:

- the current selected LabelSnapshot;
- its immediate stored predecessor, or none for version 1.

The projection itself never chooses "latest". Version selection belongs to the
caller.

## History invariants

A comparison fails closed when:

- a stored content fingerprint no longer matches the stored facts;
- version 1 names a predecessor;
- version >1 has no predecessor;
- predecessor barcode differs;
- versions are not contiguous;
- adjacent versions have the same content fingerprint;
- stored changed_fields no longer equal the deterministic recomputation.

No supported write path creates any of those states.

## Formula comparison

Step 12A reuses Step 7B for parsing and Step 7A's sole identity normalisation.
It introduces no second ingredient parser, synonym table, fuzzy match, or AI
interpretation.

When ingredients changed:

- **unchanged** — the normalised printed entry sequence is identical;
- **reordered_only** — the same multiset of printed entries appears in a
  different order;
- **ingredient_set_changed** — occurrence counts differ, with conservative
  added/removed entry counts;
- **not_comparable** — either formula cannot be parsed whole, or an entry has no
  usable canonical normalisation.

A reorder is not a concentration claim. Added/removed entries are not safety,
efficacy, or regulatory conclusions.

## Deliberate non-scope

Step 12A does not:

- infer why a manufacturer changed a formula;
- infer concentration from printed order;
- decide whether a change is good, bad, safer, stronger, or weaker;
- interpret FSSAI notices;
- subscribe a user to a product;
- send notifications;
- create a watch worker or cron job;
- add paid infrastructure.

Those belong to later Step 12 slices after change authority is stable.
