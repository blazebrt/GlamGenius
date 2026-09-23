# Supplement authority (Step 13)

Step 13 makes supplements a first-class part of the existing loop without
turning GlamGenius into a supplement adviser:

```text
SCAN → UNDERSTAND WHAT THE PACK SAYS → ORGANIZE WHAT THE CUSTOMER OWNS
     → IDENTIFY FACTUAL OVERLAP / MISSING INFORMATION → REMEMBER → MANAGE SAFELY
```

The output is **label and ownership intelligence**. It is never dosage,
treatment, diagnosis, deficiency, interaction, pregnancy or child guidance, an
intake total, an efficacy claim, a recommendation or shopping advice. Those
questions are routed to a qualified professional.

Starting authority: `origin/main` `145fccb710f4debdc9f915d1ee8f04367986b9c5`
(tree `a3cbadef676f858c68e931bac69da1af66a4a5aa`), Alembic head `k9l0m1n2o3`.
Step 13 adds **no migration**.

## Constitution loop

| Loop stage | What Step 13 serves |
| --- | --- |
| SCAN | A photo of the label of a supplement the customer owns, transcribed into unconfirmed drafts; see [The scan-first question](#the-scan-first-question). No product `LabelSnapshot` is used or created. |
| UNDERSTAND | What the label lists, as recorded; the exact compound form when the printed name fixes one; calculated package chemistry when the form fixes a formula; published research only through the governed reader. |
| DECIDE | Nothing. There is no Buy / Wait / Skip for supplements (`supplement_purchase` stays `prohibited`). |
| REMEMBER | Owned supplements, their printed label facts and who recorded each one. |
| MANAGE | Calm expiry states, factual overlap across owned products, missing information, the professional boundary. |

## Relationship to other work

- **VC-07** (`docs/VC-07_SUPPLEMENT_SAFE_UTILITY.md`) built the owned-supplement
  utility: inventory, label components, reviewed aliases, overlap, expiry,
  boundary, privacy. Step 13 keeps every VC-07 boundary and its summary
  contract (`vc-07-v1`) and adds a governed per-item detail on top.
- **Step 12** (label change authority, regulatory change authority, Product
  Watch) governs confirmed *food and skin-care* captures. Nothing in Step 13
  reads or writes those ledgers, and the supplement photo bridge creates no
  scan event, snapshot or watch. A supplement pack photographed through the
  *food* route is still treated by those layers as a food pack; Step 13 does not
  change that (see Known limits).
- **Step 14** (Full Purchase Operating System) is not started. Step 13 applies
  no purchase decision to supplements; a supplement decision would need its own
  separately governed medical, safety and product authority.

## Audit — what existed before Step 13

Answers are from the code at the starting SHA, not assumed.

1. **Which facts come from manual entry?** All of them. The only public writer,
   `POST /supplements/items/{id}/label-facts`, stores `source=user_declared`,
   `verification_state=confirmed`, `confidence=1.0`, and refuses any client
   provenance field (422).
2. **Which can come from a confirmed scanned label?** None. The two
   confirmation routes (`/scan/label/confirm`, `/scan/skin-care/label/confirm`)
   produce food-shaped and skin-care-shaped facts (`ingredients_text`,
   `nutrition_per_100g`, `serving_size`, `batch_number`, …). Neither carries a
   supplement component, a per-serving amount, or a compound form, and neither
   binds `product_category=supplements`.
3. **Can supplement inventory be tied to an exact LabelSnapshot?** No.
   `InventoryProductLink` exists, but the only writer (Step 10A
   `scan_ownership`) accepts `beauty`, `hair` and `perfumes` only.
4. **Is batch/lot preserved?** Not for supplements. Where a pack is captured,
   batch lives on the capturing account's own `ScanEvent`; it is outside the
   snapshot fingerprint, so a shared `LabelSnapshot` carries the first
   capturer's lot, which is why Step 12C anchors to the account's own capture.
5. **Can a label fact come from another user's snapshot?** No. Label facts are
   only ever written by the owning account's own request, filtered by
   `account_id` and owned item on every read and write.
6. **How does `SupplementLabelComponent` relate to Store-B label facts?** It
   does not. It is a separate, account-owned table with its own provenance
   (`user_declared` / `photo_extracted`, enforced by a check constraint).
7. **Which aliases are reviewed authority?** Two: `ascorbic acid` and
   `l ascorbic acid` → Vitamin C, written by VC-07. The other alias spellings in
   `engine.REVIEWED_ALIASES` were folded in at import from
   `knowledge.Compound.aliases`, which the knowledge file itself says nobody has
   checked, and they decided write-time identity and therefore customer-visible
   overlap. *Correction round:* that fold-in is removed; see
   [Reviewed identity authority](#reviewed-identity-authority).
8. **Which compound entries are authored drafts?** Every one. The loader writes
   each entry as a `draft` claim and each row as `unverified`, always.
9. **Which rows are human-reviewed or published?** None, in any database this
   work could legitimately inspect (see the inventory below).
10. **Can unverified absorption knowledge reach a customer today?** Before Step
    13: no customer path read `supplement_component_knowledge` at all. After Step
    13: only through the governed reader, which withholds all of it today.
11. **Are row verification and claim publication guaranteed to agree?** No.
    They are two columns in two tables with no link beyond a nullable foreign
    key. Step 13's reader requires both, plus an exact binding, and withholds
    on any disagreement.
12. **Are source URLs openable?** Unknown by construction — the knowledge file
    says no URL was followed. The shared `evidence.urls.openable_url` checks
    shape only (never the network). The reader requires it on the row and on a
    reviewed source path, and requires them to be the same URL.
13. **Can a row be read as daily intake?** The API names are `amount`, `unit`,
    `serving_text` and the engine never sums them. Step 13 returns them under
    `printed` and tests that no total, daily or intake figure exists anywhere.
14. **Can serving text be read as a recommendation?** It is stored verbatim.
    Step 13 returns it verbatim under `printed.serving_text` and the app frames
    it as "Printed serving text: “…”".
15. **Are unit variants normalised?** No — displayed only. `mcg`, `µg` and `IU`
    are never converted. (The old item screen capitalised them visually; Step
    13's surface does not.)
16. **Can one product hold several rows for one component?** Yes ("Vitamin C"
    and "Ascorbic acid" on one bottle). Overlap is counted per product.
17. **Does overlap imply excess?** No. It means the same component key appears
    on more than one confirmed owned product, and carries names only.
18. **Does deleting an item remove its facts?** The customer "remove" archives
    the item ("history retained"). Archived items' facts are excluded from
    every surface and every label-fact route (404). Deleting the item row
    cascades its facts (`ON DELETE CASCADE`, verified in the catalogue).
19. **Does privacy export include all account-owned supplement state?** Yes:
    `supplement_details`, `supplement_label_components`,
    `supplement_safety_flags`. Before Step 13 the label components went out with
    every column, including `account_id`, `source_ai_run_id`, `model_version`,
    `prompt_version` and `client_mutation_id`.
20. **What UI existed?** The item screen's "Label facts" list and entry form;
    the shelf's supplements tab (summary cards, "Repeated label components",
    "What we do not do"). Missing: provenance worded as provenance ("Confirmed
    from label" did not say who confirmed), form identity, the reason a figure
    is withheld, published research with its source, per-product overlap on the
    item, and missing-information states.

Further findings:

- `routines/service.supplement_question` (the professional-boundary route)
  called only `safety.boundary_for` — the narrow check the constitution says
  **must not** stand in for the hard handoff gate. The gate was never called for
  supplement questions.
- That narrow check and the gate together still let through, among others: "Is
  this ok for kids?", "Does this help with a disease?", "Do I have low vitamin
  D?", "I felt sick after this", "How many capsules a day?", "What if I
  overdose?", "My lab report shows low ferritin", "Should I start this?",
  "Should I stop this?", "Do I need this?", "Which supplement should I buy?"
  (asserted in `test_r_the_supplement_patterns_close_gaps_…`).
- `safety.SUPPLEMENT_FLAG_TEXT` holds "Replace it rather than using it." It is
  referenced nowhere and reaches no customer; left untouched.
- Several knowledge aliases denote a different molecular form from the entry
  they sit under: `magnesium chloride hexahydrate` and `epsom salt(s)` under
  anhydrous formulas, `ferrous sulphate dried` under anhydrous `FeSO4`,
  `calcium citrate malate` under calcium citrate, and bare `vitamin c`, `b12`,
  `vitamin b12`, `cobalamin`, `curcumin`, `coenzyme q10`, `vitamin b9` under one
  specific form each.
- The calcium citrate formula stored in the knowledge file (`Ca3C12H18O18`) is
  the tetrahydrate; a label that prints "calcium citrate" does not say which.
- The generic authoring tool records no evidence strength or rationale and
  files every new source as type `other`. A supplement claim written through it
  therefore cannot satisfy the public-knowledge path without two operator acts
  the tool has no step for.

## What Step 13 adds

| Piece | File | Purpose |
| --- | --- | --- |
| Reviewed vocabulary | `supplements/names.py` | Hand-written nutrient spellings, display names and the two VC-07 equivalents. Constants only. |
| Identity authority | `supplements/identity.py` | The one executable identity map; read-time revalidation of stored keys. |
| Form identity | `supplements/forms.py` | Explicit printed-name → compound-form table; recognised vs knowledge-eligible; package chemistry or the reason it is withheld. |
| Governed knowledge reader | `supplements/knowledge_reader.py` | The one path from knowledge to a customer; withholds on any disagreement. |
| Loader lifecycle | `supplements/knowledge_loader.py` | Binds each pristine draft to its row values; never rewrites reviewed authority; reports drift. |
| Supplement boundary | `supplements/boundary.py` | Gate → narrow check → supplement-decision shapes; supplement-only alternative copy. |
| Photo bridge | `supplements/photo.py`, `POST /api/v2/supplements/items/{id}/label-photo/transcribe` | Owned photo → draft label facts, nothing else. |
| Detail | `supplements/detail.py`, `service.detail`, `GET /api/v2/supplements/items/{id}` | One coherent, code-based customer payload per owned supplement. |
| Export | `privacy/export.py` | Customer-readable label-fact rows. |
| App | `components/inventory/SupplementDetail.tsx`, `components/inventory/SupplementPhotoReader.tsx`, `strings/supplements.ts`, `app/inventory-item.tsx` | The detail and "Read label from photo" on the owned item screen; keyed strings. |

Nothing else changed. No schema, no new table, no new provider, no worker, no
new media store, no second AI client.

## Label-fact authority and provenance

| `source` | `verification_state` | Written by | Customer sees | Drives overlap, form, chemistry, research |
| --- | --- | --- | --- | --- |
| `user_declared` | `confirmed` | the manual entry route | "You entered this" | Yes |
| `photo_extracted` | `draft` | the photo bridge, only | "Read from your photo · not confirmed yet" | No |
| `photo_extracted` | `confirmed` | the customer's confirm action | "Read from your photo · confirmed by you" | Yes |
| anything else | — | — | refused by the database check constraint | — |

The item must also be confirmed: an AI-drafted inventory item that the customer
never confirmed drives nothing, whatever its facts say.

No state reads as scanned, manufacturer-supplied, regulator-issued or verified,
because none of those is true of any supplement fact. Confirming a fact records
that the customer confirmed it and nothing grander; it never changes `source`.
An edit keeps the source and the confirmation state: an edited draft is still a
draft, and an edited confirmed photo fact is still the customer's confirmed
photo fact. Retry keys under `photo:` are reserved for the bridge; the manual
route refuses them (422), so a manual row can never be replayed as a photo one.

## The scan-first question

The brief asks whether supplement facts can be grounded in the confirmed
physical-pack authority (ScanEvent → LabelSnapshot → current-pack resolver →
Store-B facts → account/device ownership), and to build the smallest safe
bridge if not.

**Finding: the product `LabelSnapshot` cannot carry supplement components, and
must not be made to.** No confirmed label schema carries supplement structure
(mapping food `nutrition_per_100g` onto per-serving components would invent a
basis the pack does not print; tokenising `ingredients_text` is the implicit
splitting Step 7A refuses); no confirmation route binds a supplement category;
Step 10A ownership excludes supplements; and a snapshot feeds food grading and
Product Watch, so a supplement component array forced into it would put a
supplement into food decisions under a category it was never confirmed as.

**The bridge built instead: supplement label photo → drafts.** The supplement
schema already had every provenance field a photo path needs
(`photo_extracted`, `draft`, `confidence`, `source_ai_run_id`,
`model_version`, `prompt_version`, `schema_version`) and no writer. Step 13
adds the writer, scoped to an item the customer already owns:

- **Route**: `POST /api/v2/supplements/items/{item_id}/label-photo/transcribe`
  with `{media_asset_id, client_request_id}`, behind the existing
  `v2_inventory` flag and registered-account dependency.
- **Ownership**: the item must be this account's active supplement
  (`owned_supplement_item`, 404 otherwise — another account's, archived or
  non-supplement items included); the photo must be this account's active
  media asset (`media_service.get_owned_asset`, the media domain's only
  ownership check; 404 otherwise, including deleted media); the stored bytes
  must sniff as JPEG, PNG or WebP (415 otherwise). All three are checked before
  any model call.
- **Extraction**: through the existing AI gateway (`run_structured`, its hourly
  cap, its run ledger). The schema has exactly `raw_name`, `amount`, `unit`,
  `serving_text` per component, plus `components` and `confidence`, with
  `extra="forbid"` at both levels: an output carrying a nutrient, compound
  form, hydration, benefit, dose, use, interaction, purpose, deficiency or
  safety field is refused whole (503) and nothing is written. The prompt forbids
  replacing a printed name with a more specific compound. An amount is kept only
  when the printed text is a plain decimal ("1,000" is recorded as missing);
  unreadable fields stay missing.
- **Write**: every row is `photo_extracted` / `draft`, with server-owned
  provenance (run id when the gateway recorded one, model, prompt and schema
  versions, the transcription's confidence). The nutrient key is decided by the
  reviewed identity authority from the printed name — never by the model.
- **Idempotency**: the logical operation is (item, photo, `client_request_id`),
  hashed into a `photo:<digest>` key; rows are keyed `photo:<digest>:<index>`
  under the existing per-account `client_mutation_id` uniqueness, and the
  operation runs under a transaction-scoped advisory lock on that key. A retry
  returns the same drafts with `status: replayed` and no second model call;
  concurrent identical requests create one set. An operation that created no
  drafts leaves nothing to replay.
- **Payload**: the drafts through the existing fact serialiser — no media id,
  storage key, AI run id, model or account id. A gateway refusal is re-raised
  without its internal run id.
- **Nothing else**: no `ScanEvent`, `LabelSnapshot`, `ProductRecord`,
  decision, Product Result grading or Product Watch is created, and the
  supplement domain imports nothing from the product scan stack or Open Food
  Facts (both asserted).

Barcode-level supplement pack identity (for a future purchase surface) remains
a separately reviewed architecture question, and is not started here.

## Nutrient identity versus form identity

### Reviewed identity authority

Nutrient identity — which nutrient a printed name denotes — comes from one map,
`identity.REVIEWED_IDENTITIES`, composed only of reviewed, hand-written
spellings:

1. bare nutrient spellings whose identity is inherently explicit
   (`names.NUTRIENT_SPELLINGS`, e.g. "Magnesium", "Elemental iron", "CoQ10");
2. reviewed equivalents (`names.REVIEWED_EQUIVALENTS`: the two VC-07
   ascorbic-acid entries);
3. the explicit exact-form spellings in `forms.EXACT_FORMS`, each naming a
   compound of exactly one nutrient.

That is 99 spellings. Anything else keeps its own normalised literal spelling
as its identity: it overlaps only with a product printing the same words, and
is never guessed into another nutrient. No fuzzy match, no generated synonym.

**The knowledge file's aliases are authoring candidates only.** Before this
correction the engine folded all of `knowledge.raw_aliases()` into the identity
map at import, so unreviewed spellings decided customer-visible overlap —
"Triglyceride", "Ethyl ester" and "rTG" grouped as omega-3, "Turmeric extract"
and "Haldi extract" as curcumin, "Fish oil concentrate" as omega-3. That
fold-in is removed. `raw_aliases()` remains for a future review tool; no
production module reads it (asserted by an AST test), and each of those
spellings now keeps its literal identity.

**Legacy stored keys are revalidated, not trusted or repaired.** Rows written
before the correction may carry a `canonical_component_key` chosen by the old
alias set. Every customer-facing decision (overlap in the summary and the
detail, form, chemistry, knowledge) derives identity from the printed
`raw_name` through the current authority (`identity.effective_key`) and uses
the stored key only when the two agree. When they disagree the stored grouping
is not trusted and the row is not rewritten: it has no usable identity, drives
no overlap, form or knowledge, shows its nutrient as "not enough information",
and an operator-only reason (`stored_identity_disagrees_with_reviewed_authority`,
with the fact id and no customer text) is logged.

### Form identity

Form identity — which exact compound the pack prints — is resolved at read time
by `forms.resolve_form`, by exact lookup in `forms.EXACT_FORMS`. **Every entry
is written out by hand in `forms.py`** (asserted: the table's keys are exactly
the string literals in the module). Nothing is synthesised from `COMPOUNDS`:
the knowledge file is a validation target only — an entry marked
knowledge-eligible must name a subject it defines — and adding a compound there
adds no form here (asserted). Its marketing formulations ("liposomal vitamin
C", "curcumin (plain extract)", "curcumin with piperine", "curcumin
phospholipid complex", "ethyl ester (EE)", "triglyceride (rTG or natural TG)")
are not recognised at all and resolve to `not_enough_information`: the words
describe a preparation, not the compound a study measured.

Three outcomes, and no fourth:

| Status | Meaning | Example |
| --- | --- | --- |
| `exact` | The printed name denotes one recognised compound form. | "Magnesium oxide", "Ferrous sulphate", "Magnesium citrate" |
| `not_stated` | A bare nutrient name; the label as recorded states no form. | "Magnesium", "Vitamin C", "Elemental iron" |
| `not_enough_information` | Anything else, including a printed form not pinned exactly, or a row whose identity failed revalidation. | "Magnesium glycinate", "Epsom salt", "5 MTHF", "Liposomal vitamin C" |

**Recognised is not knowledge-eligible.** A printed name can be specific enough
to show back as a form and still not fix the compound a study describes.
"Magnesium citrate" does not say mono- or trimagnesium; a chelate name does not
fix what a maker chelated; "dried" ferrous sulfate is a range. Those are
recognised (shown, chemistry withheld with its reason) but not
knowledge-eligible, so they never become a knowledge join key even when a
same-named entry is published (asserted with a published "magnesium citrate"
entry: a label printing "Magnesium citrate" gets nothing, one printing
"Trimagnesium dicitrate anhydrous" gets it). A hydrate name, or an unstated
hydrate, is still the same salt, so it is eligible: hydration changes the
arithmetic, not the compound.

The table today (`forms.py`, `FORM_IDENTITY_VERSION = "step-13-forms-v2"`) holds
77 printed spellings: 55 mineral spellings (28 with a fixed formula, whose
chemistry is calculated; 27 whose chemistry is withheld with a reason) and 22
non-mineral spellings (vitamin, CoQ10, curcumin and omega-3 forms, which have no
elemental arithmetic). 64 spellings are knowledge-eligible, covering 27 of the
38 draft knowledge entries; 13 are recognised only (magnesium citrate, magnesium
malate, the three bisglycinates and their spelling variants, the dried ferrous
sulfate spellings, carbonyl iron). The other 11 knowledge entries — the
formulations above and the forms that are recognised only — have no printed
spelling that reaches them, and stay unreachable until a reviewer adds one.

Form-specific chemistry and knowledge are looked up by the exact
`(nutrient, form)` pair and never by nutrient key. A form whose nutrient
disagrees with the fact's revalidated key is refused, not re-keyed.

## Hydration, salt and composition ambiguity

Package chemistry is withheld — with the reason stated — whenever the printed
name does not fix one molecular formula:

| Reason | Printed names (examples) |
| --- | --- |
| `hydration_not_stated` | ferrous sulphate/sulfate, ferrous gluconate, zinc sulphate/sulfate, zinc gluconate, magnesium chloride, magnesium sulphate/sulfate, calcium citrate, calcium lactate, trimagnesium dicitrate |
| `salt_form_not_stated` | magnesium citrate (mono- or tri-), magnesium malate |
| `composition_varies` | magnesium/ferrous/zinc bisglycinate (chelate make-up differs by maker), dried ferrous sulphate (a range, not a formula), carbonyl iron (a purity) |
| `exact_formula_not_established` | zinc picolinate, magnesium L-threonate |
| `form_not_stated` | "Magnesium", "Iron", "Zinc", "Calcium" |

No hydrate, salt, formula or elemental equivalent is ever inferred.

## Deterministic chemistry

When the printed name fixes the formula — e.g. magnesium oxide (MgO, 60.3%),
ferrous fumarate (32.9%), ferrous sulphate heptahydrate (20.1%), magnesium
chloride hexahydrate (12.0%), calcium citrate tetrahydrate (21.1%) — the detail
shows the element's share of the compound's weight, computed by
`chemistry.elemental_percent` from IUPAC standard atomic weights. The hydrate
figures are asserted equal to the knowledge base's independently written
hydration notes.

It is labelled "Package chemistry … This is chemistry, not how much your body
takes in." It is **never** multiplied by a printed amount: a label may already
print that amount as the element, and the product would be an intake figure.
It is never presented as absorbed amount, amount the user gets, effective dose,
daily intake or recommended amount. It is not sourced clinical evidence and is
not presented as one. Non-mineral forms (vitamins, CoQ10, curcumin, omega-3)
have no element share and show none.

## Empirical evidence and the publication gate

Absorption, relative bioavailability and blood-level effects are empirical and
reach a customer only through `knowledge_reader.read_form_knowledge`, which
returns `published` only when **all** hold:

1. requested by an exact `(nutrient, form)` pair;
2. the row exists for exactly that pair, is `confirmed` (not `unverified`, not
   `disputed`), and carries a figure, a confidence and an openable URL;
3. the row's claim is about exactly this form (domain `supplements`, subject
   type `supplement_component`, subject key = the row's form), tier
   `clinically_studied` on both sides, not AI-generated, strength
   `strong`/`moderate`/`limited`;
4. `claim_is_public_knowledge_path(claim)` — published by a named person after
   approval, supported, graded with a rationale, all six verification
   checkpoints recorded and no doubt left;
5. the claim's binding (`structured_value.supplement_form_knowledge`) equals
   the row's current values exactly, and the reviewed claim text contains the
   sentence shown;
6. a reviewed supporting source path of type peer-reviewed research,
   systematic review, official guideline, government reference or professional
   consensus — never `other` or a manufacturer document — with the row's exact
   URL, a title, a publisher and a licence note (`source_path_is_public_knowledge`).

`confidence` is never verification. A URL string is never proof of opening.
Row verification is never publication. `disputed` has no publication path in
the evidence contract (a published claim must be `supported`), so a disputed
row is withheld; a disagreement a reviewer published inside a supported claim
is shown as "Sources differ: …". When shown, research always carries its source
name, publisher and an open link.

Every withheld entry has a stable operator reason code (`WithheldBecause`).
Genuine disagreements between the two authorities are logged as
`supplement_knowledge_withheld` with the reason, the pair and the internal ids;
plain unreviewed drafts are the normal dormant state and are not logged.
Nothing is repaired.

The loader writes the binding onto each draft it owns, and refreshes it only
while the claim is still a draft with no recorded verification. Editing a
published claim creates a new draft version without a binding, so the entry
disappears from customers until that version is itself bound, reviewed and
published.

### Loader lifecycle: reviewed authority is never rewritten

The loader is release-owned: it may keep an **unreviewed draft** in step with
the knowledge file, and nothing else. A row is rewritten only while it is
`unverified` **and** both the claim it is linked to and the claim it would be
linked to are pristine drafts (never approved, rejected, published or
attested). Anything else — a `confirmed` or `disputed` row, or a claim a person
has touched — is left exactly as it is: no value, no verification state, no
claim link and no binding changes.

Before this correction the loader rewrote every row from the file and reset
`verification` to `unverified` on every run, so a rerun after review silently
destroyed human-reviewed authority (the reader then failed closed, but the
review was gone).

Where the file now differs from a preserved row, the difference is **reported,
not applied**: the loader logs `supplement_knowledge_reviewed_row_drift`
(reason `reviewed_row_drift`, the row id, the form and the names — not the
values — of the differing fields) and its summary carries
`reviewed_rows_preserved` and `reviewed_row_drift` counts for a person to act
on. Tests prove a confirmed + published row, a disputed row, and a row under an
approved claim all survive a rerun unchanged, including under a drifted file.

### How an entry could ever become visible

Only by people, in this order: run the loader (drafts); classify the source
type and record its licence note; grade the claim (strength and rationale);
approve (authoring tool, openable URL required); record all six verification
attestations; publish; mark the row `confirmed`. Two of those steps — source
classification and grading — have no supplement tooling today. A reviewed
supplement authoring adapter (as Step 7A has for identity) is the missing
piece, and is not built here.

## Current knowledge review-state inventory

From `knowledge.COMPOUNDS` and from databases this work could legitimately
inspect: the local PostgreSQL 16 test database after `alembic upgrade head` and
the reference-data seed, and the same after running the loader as a test
fixture. **Production was not touched and its state is unknown.**

| | Fresh migrated + seeded DB | After running the loader |
| --- | --- | --- |
| Knowledge rows | 0 | 38 |
| `unverified` rows | 0 | 38 |
| `confirmed` rows | 0 | 0 |
| `disputed` rows | 0 | 0 |
| `draft` claims | 0 | 38 |
| `approved` claims | 0 | 0 |
| `published` claims | 0 | 0 |
| Tier `not_enough_information` | — | 15 (no source; cannot be approved) |
| Tier `clinically_studied` (carries a figure) | — | 23 |
| Customer-visible entries | 0 | **0** |

Of the 23 entries with a figure, the author rated confidence high for 4, medium
for 14 and low for 5, and recorded a disagreement between sources for 16. None
of those ratings is a review. No existing absorption entry became
customer-visible in Step 13, and nothing was marked confirmed, approved or
published.

## Overlap semantics and no totals

Overlap means: one component key is listed on more than one of the customer's
own confirmed products. It is counted per product (two rows on one bottle are
one product), carries printed names only, and is stated as "Listed on N
products you recorded. This compares names only. Amounts are never added
together." It never says too much, unsafe, stop one, a limit or an excess.

No amount is ever added to another, converted, per-day'd or combined with
chemistry, because serving instructions differ, the customer may not use every
product, frequency is unknown, label bases differ and personal context is out
of scope. Tests search every payload for sums and for any total/daily/intake
key.

## Professional boundary

`supplements/boundary.evaluate` is the one supplement boundary, used by the
question route, the VC-07 summary and the detail. Its alternatives are about
records and labels only — "What supplements you recorded", "What the package
label says, as you recorded it", "Which of your products list the same
component", "Expiry dates you recorded", "Which label details still need your
confirmation" — and it carries its own professional-boundary sentence. The
generic routine boundary's "The order to use your products in" and food ideas
are right for skin care and wrong here (they can read as supplement sequencing,
timing or pairing), and its non-boundary line no longer says "how often you
take them". Every boundary payload is tested free of order, timing, "take
first/together", food pairing and "best time" wording. The decision order:

1. the constitutional hard handoff gate (`hard_handoff.evaluate`) — its decision
   and message stand;
2. the narrow medical-question check (`safety.needs_professional`), unchanged;
3. for questions only, the supplement-decision shapes the first two
   demonstrably miss: start/stop, need, how many/much/often, overdose, too much,
   deficiency and lab results in ordinary words, conditions, reactions,
   children, efficacy, and which one to buy.

A customer's own item note is a record, not a question: the gate and the
narrow check apply to it, the question shapes do not ("Better sleep" is not a
request to rank products; "For my thyroid" hands off). Responses never echo the
person's words, and the reason is a rule-family code.

Behaviour change, intentionally stricter: "I take this after breakfast." now
hands off, because the gate fails closed on a bare taking-frame. The VC-07 test
was updated to keep a non-boundary example and to assert the new handoff.

## Privacy

- **Cross-account**: every read and write filters by account and owned item;
  another account gets 404 on detail, list, create, patch, confirm and delete,
  and never appears in overlap.
- **Export**: label facts go out with printed name, nutrient key, printed
  amount, unit, serving text, provenance, confirmation state, schema version and
  timestamps. `account_id`, `source_ai_run_id`, `model_version`,
  `prompt_version` and `client_mutation_id` are left out (the Step 12C
  `product_watches` precedent). Global knowledge and its review metadata are not
  personal data and are not exported.
- **Detail payload**: no account id, AI run id, knowledge row id, evidence
  claim id, reviewer, storage key or confidence.
- **Item removal / account deletion**: see audit answer 18; account deletion
  cascades through `accounts` and leaves other accounts' facts intact.
- The photo bridge reads only the caller's own active media asset through the
  media domain's single ownership check; another account's or a deleted photo
  is a 404 before any model call. Supplement rows store no media id and no
  storage key; the AI run id stays server-side (never in a payload or export).
  The photo itself remains an ordinary media asset under the existing media
  deletion and export rules.
- No health-profile field is read or added. Age, sex, weight, conditions, lab
  values, medications and pregnancy status play no part in any output.

## ODbL wall

Open Food Facts data never becomes a supplement label fact. The supplement
domain imports nothing from Store A (asserted by AST), its tables live on the
main metadata, the `source` check constraint admits only `user_declared` and
`photo_extracted`, and a Store A product with a matching name and supplement
ingredients produces no fact, no component and no text on the detail.

## Failure modes

| Failure | Behaviour |
| --- | --- |
| Row unverified, disputed, figure-less or URL-less | `not_enough_information` |
| Claim missing, draft, approved-only, superseded, AI-generated, ungraded | `not_enough_information` (logged when the row side is confirmed) |
| Claim about another form, binding missing or different, reviewed text differs | `not_enough_information`, logged |
| Source unclassified, unreviewed, inactive, unlicensed, unopenable or a different URL | `not_enough_information`, logged |
| Printed name unknown or nutrient key disagrees | form `not_enough_information`; no chemistry, no research |
| Hydrate/salt/composition not fixed | chemistry withheld with its reason |
| Fact or item unconfirmed | shown as unconfirmed; drives nothing |
| Detail request fails in the app | the existing editable label list still works |
| Stored identity disagrees with the reviewed authority | no overlap, form or knowledge for that row; nothing rewritten; logged |
| Loader run over reviewed rows | rows, links, claims untouched; drift logged and counted |
| Photo: item or media not owned, deleted or archived | 404 before any model call; nothing written |
| Photo: bytes are not an image | 415 before any model call; nothing written |
| Photo: model output malformed or carries a judgement field | 503 without a run id; nothing written |
| Photo: retry of the same operation | same drafts returned; no second model call |

## Proof

- `backend/tests/test_step13_supplements.py` — the brief's matrix A–X against
  PostgreSQL 16 and the real routes, plus the correction round (reviewed
  identity and legacy revalidation, explicit and eligibility-gated forms, the
  loader lifecycle, boundary copy). Where an entry has to be *published* for a
  test, the test walks the real authoring workflow and performs the two
  untooled operator acts (grading, source classification) as labelled fixtures.
  No test claims a real entry was reviewed.
- `backend/tests/test_step13_supplement_photo.py` — the photo bridge: owned
  photo → drafts with server-owned provenance, ownership and media refusals,
  malformed and judgemental output refused whole, drafts driving nothing,
  confirmation and edits keeping the source, replay and concurrency, no scan
  stack or Store A, export and deletion. The AI provider is stubbed; no live
  model call.
- `backend/tests/test_supplement_knowledge.py` — its alias test encoded the
  defect (unreviewed aliases resolving to nutrients); it now asserts the
  reviewed spellings resolve and the unreviewed ones keep their literal
  identity.
- `backend/tests/test_vc_07_supplement_api.py` — stricter boundary
  expectations (gate handoff; neutral non-boundary wording).
- `frontend/src/__tests__/supplementDetail.test.tsx`,
  `supplementPhotoReader.test.tsx` — provenance, drafts, printed units and
  serving text, chemistry wording, research only with an openable source,
  factual overlap, the boundary, the photo action (camera permission only on
  choice, retry replays), and keyed-string sweeps.
- A mutation pass applied 53 deliberate breaks to the real code
  (and two to the real database), one at a time; every one was caught.

## Rollback

Revert the Step 13 commit. There is no migration and no data change: the
knowledge binding lives in draft claims' `structured_value` under its own key,
which the pre-Step-13 code never reads, and the export and route changes are
code-only. The VC-07 summary contract is unchanged.

## Known limits

- Supplement pack identity by barcode is not built: the photo bridge is scoped
  to an item the customer already owns and carries no pack version or lot.
- Pre-existing and unchanged: a supplement barcode scanned and confirmed
  through the *food* label route is graded and watched as a packaged food.
- The executable identity map is small on purpose (99 spellings). Common
  printed spellings outside it ("Magnesium glycinate", "Vit D3", "5 MTHF")
  keep their literal identity until someone reviews and adds them.
- Rows written before the correction under the old alias set lose their
  grouping (fail closed) until the customer re-enters or renames them; nothing
  migrates them.
- No supplement authoring adapter: source classification and grading have no
  tooling, so no knowledge entry can become visible without it. All 38 remain
  dormant.
- The exact-form table is code authored in this step: chemistry nomenclature,
  not evidence, awaiting independent review.
