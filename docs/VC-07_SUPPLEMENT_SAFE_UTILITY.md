# VC-07 — Supplement Safe Utility

VC-07 is an owned-supplement utility. It makes package facts easier to read
without becoming a treatment, dosage, nutrition, or shopping product.

## Supported

- Owned supplement inventory, brand, purpose note, and expiry date
- Structured label facts: component, printed amount, unit, and serving text
- Conservative deterministic normalization and reviewed aliases only
- Label overlap awareness across confirmed owned products
- Missing-information states and calm expiry language
- Technical provenance and customer-visible confirmation state
- Professional escalation for health-like questions
- Privacy export and account/item cascade deletion

## Not supported

- Dosage, treatment, diagnosis, deficiency, or medicine interaction answers
- Pregnancy or breastfeeding advice
- RDA, EAR, UL/TUL, deficiency thresholds, or nutrient totals
- Intake calculations, elemental-form conversion, or efficacy claims
- Supplement recommendations, replacement products, shopping, or reminders

## Why UL/TUL remains disabled

The reviewed evidence contracts currently provide applicability dimensions for
general evidence, but not a separately reviewed, versioned nutrient-reference
system capable of safe individualized upper-limit comparison. VC-07 therefore
keeps UL/TUL, RDA, and EAR behavior deliberately inactive rather than guessing
or manufacturing a medical reference system.

Amounts stored by this domain are printed package facts. They are displayed per
product and never summed into an intake or daily total.

## Relationship to Step 13

Step 13 (`docs/architecture/SUPPLEMENT_AUTHORITY.md`) builds on this utility
and keeps every boundary above. It adds a governed per-item detail
(`GET /api/v2/supplements/items/{id}`): provenance worded as provenance, exact
compound-form identity from the printed name, calculated package chemistry only
when the printed name fixes one formula, published research only through one
governed reader (which withholds every existing entry today), per-product
overlap, and missing-information states. The professional-boundary route now
calls the constitutional hard handoff gate first. The `vc-07-v1` summary
contract is unchanged.

Step 13 also restores "reviewed aliases only" above: the knowledge file's
unreviewed aliases, which had been folded into overlap identity at import, no
longer execute, and stored keys are revalidated against the reviewed authority
before they group anything (normalization version `vc-07-r2`). And the dormant
`photo_extracted` path now has its one writer: a photo of an owned supplement's
label becomes draft label facts that drive nothing until the customer confirms
them.

"Intake calculations, elemental-form conversion" above remain unsupported:
Step 13's package chemistry is the element's share of a compound's weight, a
property of the compound, and is never multiplied by a printed amount.
