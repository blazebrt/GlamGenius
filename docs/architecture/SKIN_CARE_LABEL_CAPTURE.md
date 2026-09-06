# Confirmed skin-care label capture, and the category bound to it

*Step 8J. The physical bridge between a phone camera and the governed Step 7/8
decision chain.*

Everything the FOR YOU engine will eventually say about a skin-care product is
read from one row: a `LabelSnapshot` carrying the ingredient list somebody
photographed. This document is about how that row comes to exist, and about the
one extra fact it now carries — `product_category` — which decides what the
formula in it is allowed to be interpreted as.

Nothing described here returns Buy, Wait or Skip. Capture makes the input
trustworthy; deciding is a later milestone.

---

## The path, end to end

```
photograph a skin-care ingredient label
  → POST /api/v2/scan/skin-care/label/transcribe   (AI transcribes visible facts)
  → the person reads the draft against the pack in their hand
  → POST /api/v2/scan/skin-care/label/confirm      (three identifiers, no facts)
  → one ScanEvent, outcome = label_captured
  → one versioned LabelSnapshot
  → facts carry product_category = skin_care
```

Transcription writes nothing at all — no scan event, no product record, no
snapshot, no category. Confirmation writes all of it, from the *stored*
transcription rather than from the request.

---

## Why packaged-food transcription was not reused

`POST /api/v2/scan/label/transcribe` already reads a label. It is the wrong
authority for a moisturiser, and widening it would have been the wrong fix.

Its schema is a packaged food pack: `nutrition_per_100g`, `nutrition_basis`,
`serving_size`, `net_quantity`, `fssai_licence`, `veg_mark`, `allergen_text`,
`mrp_text`. Its prompt asks the model, in those words, to transcribe a packaged
food label, and tells it where to find a fourteen-digit FSSAI licence. None of
that exists on a face cream.

A hybrid would have produced one prompt asking for two incompatible things and
one schema in which most fields are meaningless whichever photo arrives — and
the ambiguity would not have stayed in the schema. A model asked for nutrition
values that are not printed is a model being invited to supply them.

So food capture stays food capture, byte-for-byte. Skin care gets
`app/domains/product/care_extraction.py`: its own feature
(`skin_care_label_transcribe`), its own prompt and schema version
(`skin-care-label.v1`), and four transcribed fields — `product_name`, `brand`,
`product_type`, `ingredients_text` — plus the model's own account of how well
the photograph read.

Both paths use the same AI gateway, the same media ownership check, the same
`ScanEvent`/`LabelSnapshot` persistence and the same pack-context authority.
Two schemas, one machine.

---

## Why the AI does not decide the category

The model is never asked what kind of product it is looking at, and
`ExtractedSkinCareLabel` has nowhere to put an answer.

This is not a doubt about the model's accuracy. It is that the category is not
a fact about the photograph at all. It is a statement about how the formula in
it is to be interpreted, and interpreting a formula is what the whole governed
chain downstream exists to do carefully. A category guessed from a picture of a
tube would enter that chain wearing the same clothes as a category a person
established, and no later layer could tell them apart.

The same argument rules out inferring it from the ingredients or from the
product name. Petrolatum is in ointments, in lip balm and in hair pomade;
"cream" appears on a face cream and on a hair cream. `product_category` is
never derived from `product_name` or `ingredients_text`, anywhere, and
`personal_applicability_category_from_label` refuses aliases, casing variants
and untrimmed values for exactly this reason: a near-match is a guess.

---

## Why the route itself is the category

The category authority is the person, and the structured form of their decision
is *which capture flow they entered*. A user who taps "Skin Care" and confirms
what they photographed has made an explicit, reviewable statement. The server
records it as a constant attached by the confirmation service:

```python
CATEGORY_FACT_KEY = "product_category"
SKIN_CARE_CATEGORY = "skin_care"
```

There is no `category` request field, no `product_category` body key, and no
query parameter — on either route. `ConfirmSkinCareLabelBody` carries three
identifiers and forbids extras, so an attempt to send one is a `422`, not a
silently ignored key. Static tests hold that shut.

V1 supports exactly one category through this route. Hair care and cosmetics are
not "not yet mapped"; they have no capture flow, and adding one is a deliberate
change with its own review. There is no subtype taxonomy, no classifier, and no
fuzzy mapping. One route, one persisted category.

---

## Why the category is persisted in the label facts

Step 8B takes its category from its caller:

```python
interpret_label_snapshot_for_account(session, snapshot, account_id=..., category=...)
```

That is fine *inside* the server, where the caller is our own orchestration.
It stops being fine the moment a client can choose it. A customer API shaped
like `GET /for-you/{barcode}?category=hair_care` would let the same captured
formula be re-asked under a category nobody confirmed, and the evidence engine
would answer conscientiously — about the wrong question.

So the category is written down at the moment it is established, next to the
facts it qualifies, and later callers read it. They do not tell the server what
it should have been. `personal_applicability_category_from_label` is the single
deterministic adapter that performs that read, and its refusals are absolute:
anything that is not exactly `skin_care` yields `None`, which means *not
established*, never *pick something*.

---

## Why the category participates in the content fingerprint

`product_category` is in `CONTENT_FACT_FIELDS`, so it is part of what a label
version *is*:

| Facts | Fingerprint |
| --- | --- |
| `{ingredients_text: "Petrolatum", product_category: "skin_care"}` | A |
| `{ingredients_text: "Petrolatum", product_category: "hair_care"}` | B ≠ A |

Without this, the two would be the same `LabelSnapshot`. The second would
inherit the first's version number, its lineage, its `changed_fields` history
and every downstream statement made about it — and the change of meaning would
leave no trace anywhere. "Petrolatum, as a skin-care product" and "Petrolatum,
as a hair-care product" are two different things to decide about, and the
identity of a label version has to be able to say so.

**Existing food snapshots are untouched.** Canonicalisation drops absent values
before hashing, so a food fact dictionary that has never heard of
`product_category` produces exactly the fingerprint it always did. Nothing is
backfilled, nothing is rewritten, and there is no migration. Absence is not
read as `packaged_food` — absence means the category was never established, and
inventing one would be the very thing this design refuses.

---

## Why a global snapshot is not physical-pack authority

`latest_label_snapshot` answers "what is the newest thing anybody has published
about this barcode". That is the right question for the product science and the
wrong one for the pack in somebody's hand: the row may be a stranger's
photograph of a stranger's packet.

`pack_context.current_pack` is unchanged by this milestone and remains the only
authority. It requires this device's **newest** scan of this barcode to be a
genuine confirmed capture, on three counts that must all hold: outcome
`label_captured`, a non-empty `label_facts` object, and a non-null `ai_run_id`.

Two consequences worth stating, because both are tested:

- A later **plain** barcode scan on the same device withdraws pack authority. A
  plain scan means a different physical packet is in the hand now, and reaching
  backwards to last week's capture would attach the wrong lot to today's tube.
  A fresh confirmation restores it.
- `product_category` is **not** part of confirmation provenance. It says what
  confirmed facts mean, never that they were confirmed. A hand-written row
  carrying it, without an `ai_run_id`, proves nothing at all.

There are now two legitimate writers of `label_captured` — the food
confirmation route and the skin-care one — and the three conditions did not
move to accommodate the second.

---

## Idempotency, and what it may not hide

`client_scan_id` exists so an offline queue can send the same confirmation twice
without writing it twice. An exact replay returns the original scan id, the
original label snapshot, an unchanged confirmation count, and `created: false`.

It does **not** exist to let a second, different confirmation inherit the
first's identity. If the stored event for `(device_id, client_scan_id)`
disagrees on the barcode, the AI run, or the confirmed facts, the request fails
with `409` and names the field that differs. Returning the older row would
quietly discard a new claim about a physical pack, which is the one thing a
capture system must never do.

---

## Device and account binding

Confirmation requires both a signed-in account and a registered device, and the
device must be claimed by *that* account. Two refusals, told apart because the
remedies differ:

| State | Result |
| --- | --- |
| No device token | `401 DEVICE_UNKNOWN` |
| Device unclaimed | `403 device_unclaimed` — claim it through the existing flow |
| Device claimed by another account | `403 device_claimed_by_another_account` |

The route deliberately does **not** auto-claim. Silently attaching a phone to
whoever first confirmed a label on it is how a shared or borrowed handset
acquires the wrong owner, and a confirmed capture is an assertion about what
*that* device's holder was looking at.

---

## Nothing is trusted from the request

The confirmation body carries a barcode, an AI run id and a client scan id. The
facts come from the stored `AIRun`/`AIRunOutput` pair, which must satisfy all of:

- the run exists, and its output exists
- both belong to the calling account
- the run succeeded and passed validation
- the run's feature is `skin_care_label_transcribe`
- run and output agree on their schema version
- that agreed version is in `CONFIRMABLE_SCHEMA_VERSIONS`

The stored payload is then parsed through `ExtractedSkinCareLabel` again rather
than trusted as stored, so direct database corruption fails closed instead of
becoming a confirmed pack fact.

A food run cannot confirm a skin-care pack and a skin-care run cannot confirm a
food pack: each route checks its own feature, so the two schemas cannot
cross-confirm.

**Ingredients are required to confirm.** A photograph that yielded no readable
ingredient list is still a legitimate *draft* — the person tried, and saying so
is more useful than an error — but it cannot become a Step 7/8-capable
snapshot, because a formula is the whole analytical content of a skin-care
label and there is nothing to invent one from. The draft says
`ingredients_readable: false`; confirmation returns `422`.

---

## What is deliberately not stored

The confirmed label facts are exactly:

```json
{
  "product_name": "…",
  "brand": "…",
  "ingredients_text": "…",
  "product_category": "skin_care"
}
```

`product_name` and `brand` appear only when the pack declared them; an unread
brand stays absent rather than becoming an empty string, so a later layer can
tell "no brand printed" from "brand not read".

The model's `confidence`, `uncertain_fields`, `photo_quality_notes` and
`product_type` are **not** persisted here. They describe the photograph and the
model's own hesitancy, not the pack, and a canonical label version is a
statement about the pack. They are shown to the person while they review the
draft, which is where they are useful, and they remain in the AI run ledger,
which is where they are auditable.

Declared active percentages are absent from V1 entirely. Transcribing one is not
the hard part; the hard part is that a percentage only means something beside
reviewed evidence about that substance at that strength, and none of that exists
yet. Storing the number early would invite a later layer to read it as though a
reviewer had stood behind it.

No image bytes are written anywhere — whole, truncated or hashed. The photo goes
to the gateway and is dropped, as on every other capture path in this product.

---

## Why there is no personal verdict yet

Step 8J deliberately stops at a trustworthy input. It does not import the
Step 8I knowledge pack, does not import the Step 8H release runtime, and does
not look up whether any release is active. Capture behaves identically with zero
active releases and with one, because a person confirming what a pack says is
not yet asking what it means for them.

The Step 8B readiness proof in the test suite goes exactly one step further and
no further: a real captured snapshot, a real trusted Step 8A context, and a real
call into `interpret_label_snapshot_for_account` with the category read from the
snapshot itself — proving the bridge reaches the governed engine and carries the
exact label-version provenance with it. It asserts provenance. It asks for no
verdict.

Step 8I remains inactive. The three production registries —
`PERSONAL_DECISION_SEMANTIC_RULES`, `PERSONAL_DECISION_POLICY_RULES` and
`PERSONAL_DECISION_EXPLANATION_RULES` — are still empty tuples, an ordinary
reference seed still produces zero Step 8I claims and zero releases, and nothing
in this milestone authors or activates anything.

---

## Next

The customer personal-decision API: current pack → category-bound
`LabelSnapshot` → Step 8A → Step 7B/7C → Step 8B → the active Step 8H release →
Steps 8C/8D/8E/8F → reviewed copy. It will read `product_category` from the
snapshot. It will not accept one.

---

## Where the code is

| File | Responsibility |
| --- | --- |
| `backend/app/domains/product/care_extraction.py` | Bounded AI transcription: schema, prompt, boundary |
| `backend/app/domains/product/care_capture.py` | Stored-run revalidation, device/account binding, confirmation orchestration, the category adapter |
| `backend/app/api/v2/skin_care_scan.py` | The two routes, kept thin |
| `backend/app/domains/product/service.py` | `product_category` in `CONTENT_FACT_FIELDS` and the changed-field map |
| `backend/app/domains/product/pack_context.py` | Unchanged authority; comments now name both confirmation surfaces |
| `backend/tests/test_step8j_skin_care_label_capture.py` | The proofs above |

A separate API module does not create a new domain. The domain logic stays under
`app/domains/product/`.
