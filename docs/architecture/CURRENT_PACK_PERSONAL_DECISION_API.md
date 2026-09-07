# The FOR YOU answer for the pack in somebody's hand

*Step 8K. The first customer surface over the governed Step 8A–8H chain.*

One endpoint:

```
POST /api/v2/scan/skin-care/for-you
```

It answers a single question — *what does the reviewed knowledge say about the
pack this device just confirmed, for this person* — and it derives every input
to that question itself.

---

## The contract

**Request**

```json
{
  "barcode": "8901030000011",
  "safety": {
    "pregnancy": true,
    "breastfeeding": null,
    "medication_involved": null,
    "diagnosed_condition_involved": null,
    "subject_is_child": null,
    "stated_age": null
  }
}
```

`safety` is optional. Every field is a strict boolean or a strict integer;
`extra="forbid"` on both models.

**Response** (the presentable case)

```json
{
  "barcode": "8901030000011",
  "product_category": "skin_care",
  "copy_version": "for-you-copy.v1",
  "pack": {
    "is_proven": true,
    "current_pack_scan_id": "…",
    "label_snapshot_id": "…",
    "label_snapshot_source_scan_id": "…",
    "label_snapshot_version": 1,
    "content_fingerprint": "…"
  },
  "result": {
    "status": "decision_presentable",
    "reason": "reviewed_explanation_available",
    "action": "buy",
    "verdict_key": "for_you.verdict.buy",
    "verdict_text": "BUY",
    "reason_key": "for_you.skin_care.petrolatum.dry_skin.dermatologist_guidance",
    "reason_text": "For dry skin, dermatologist guidance includes petrolatum among ingredients to look for in a cream or ointment.",
    "citation": {
      "source_key": "…", "title": "…", "publisher": "…",
      "canonical_url": "…", "locator": "…",
      "publication_date": null, "version_or_revision": "…", "jurisdiction": null
    },
    "handoff": null
  },
  "release": { "id": "…", "version": 1, "content_hash": "…" }
}
```

### Statuses

Five come straight from Step 8F, unrenamed: `decision_presentable`,
`not_enough_information`, `not_enough_decision_policy`, `not_enough_explanation`,
`handoff_required`. Three are this layer's own, because Step 8F cannot know
about them: `pack_not_confirmed`, `pack_category_not_supported`,
`not_enough_copy`.

**For every status except `decision_presentable`**, `action`, `verdict_key`,
`verdict_text` and `citation` are all `null`. A handoff additionally carries its
envelope, and still no action and no citation.

---

## Authority order

```
authenticated account
  → registered device
  → device claimed by this account
  → this device's current pack
  → the pack is a confirmed label capture
  → the exact label version behind that capture
  → the category recorded on that version
  → Step 8A personal context (live)
  → Step 8B governed evidence (live)
  → the one active Step 8H release
  → Steps 8C / 8D / 8E / 8F
  → reviewed customer copy
```

Nothing lower may override anything higher, and the client supplies none of it.

---

## Why `current_pack` is the authority, and global latest is forbidden

Step 8J established the invariant this endpoint is built on:

```
latest_label_snapshot(barcode)  ≠  the pack in this person's hand
```

`latest_label_snapshot` answers "what is the newest thing *anybody* published
about this barcode". That is the right question for product science and the
wrong one here: the row may be a stranger's photograph of a stranger's packet.

So the endpoint begins at `pack_context.current_pack(session, barcode, device_id)`,
which requires this device's **newest** scan of this barcode to be a genuine
confirmed capture — outcome `label_captured`, non-empty `label_facts`, non-null
`ai_run_id`. A static test asserts that neither the route nor the resolver so
much as names `latest_label_snapshot`.

Consequences, each tested:

| Situation | Answer |
| --- | --- |
| Device never scanned this barcode | `pack_not_confirmed` |
| Only a plain scan | `pack_not_confirmed` |
| Confirmed, then a later plain scan | `pack_not_confirmed` |
| Another device confirmed it; a global snapshot exists | `pack_not_confirmed` |
| Forged `label_captured` row with no `ai_run_id` | `pack_not_confirmed` |

`pack_not_confirmed` is a normal customer state, not an error. No release is
consulted, and no global label is reached for: an answer about some other packet
is not a better answer than none.

---

## Why the pack's scan and the label snapshot may be different rows

Step 3 deduplicates identical label content. Confirming the same formula for the
same barcode a second time returns the snapshot that already holds that content
rather than writing a near-identical row — so a perfectly valid, proven capture
can own no snapshot at all.

The response therefore reports **two** identities and never conflates them:

- `current_pack_scan_id` — the confirmation that proves physical possession.
- `label_snapshot_source_scan_id` — the capture whose content became the
  semantic version.

### Why "the latest snapshot with this content" is the wrong repair

Snapshots are global to a barcode and anybody can add one. If a stranger
photographs the same formula next week, a "latest matching" lookup would
silently re-attribute this person's capture to a row that **did not exist when
they confirmed it** — future observations rewriting past provenance.

`resolve_current_pack_label_snapshot` therefore:

1. **Exact event first.** A snapshot whose `scan_event_id` is the current pack's
   event, verified on barcode, canonical facts and fingerprint together.
2. **Dedup fallback.** Otherwise the latest semantic version whose *source
   capture* happened at or before the current pack's own capture, ordered
   exactly as `pack_context` orders scans — server `created_at`, then `id`.
3. **Otherwise it raises.** A proven pack with no resolvable version is a broken
   invariant, not a customer state. It answers `503 FEATURE_UNAVAILABLE` and
   never downgrades to `pack_not_confirmed`, which would tell the person
   something false and hide a real defect.

The adversarial case is a required test: Device A confirms formula A twice (the
second deduplicates), then Device B publishes formula B (v2) and formula A again
(v3). Device A's answer must use **v1**, not v3.

---

## Why the category is read from the snapshot

Step 8B takes its category from its caller. That is fine inside the server and
unacceptable at the edge: `?category=hair_care` would let the same captured
formula be re-asked under a category nobody established, and the evidence engine
would answer conscientiously about the wrong question.

So the category comes from `care_capture.personal_applicability_category_from_label(snapshot)`
— the exact Step 8J adapter, exact string only, no aliases, no casing variants,
no untrimmed matches, no default. `product_name`, `brand` and `ingredients_text`
are never consulted: petrolatum is in ointment, lip balm and hair pomade.

Anything that is not exactly `skin_care` yields `pack_category_not_supported`,
with `product_category: null` — the server has not established a category it can
answer for, and echoing an unsupported one would imply otherwise.

---

## Why safety input is structured, and why it is ephemeral

The hard-handoff authority in `routines/hard_handoff.py` reads language, and it
stays the **only** implementation — a second one would drift, and a safety gate
that disagrees with itself is worse than either version alone.

But its free-text input is not something to expose to a customer API. So this
layer accepts six flags and converts the `true` ones into the smallest canonical
tokens that authority already recognises:

| Flag | Token | Handoff reason |
| --- | --- | --- |
| `pregnancy` | `pregnancy` | `pregnancy` |
| `breastfeeding` | `breastfeeding` | `breastfeeding` |
| `medication_involved` | `medication` | `medication` |
| `diagnosed_condition_involved` | `diagnosed condition` | `clinical_condition` |
| `subject_is_child` | *(structured)* | `child_subject` |
| `stated_age` < 12 | *(structured)* | `age_under_minimum` |

`false` and `null` are both "nothing stated"; only `true` produces a token,
because inventing a negative assertion from an unanswered question would be
worse than silence.

There is **no** field for a medicine name, a diagnosis, a note or any free text.
The product does not need to know *which* medicine in order to hand over to a
clinician, and collecting it would create a health record this milestone has no
business holding. Booleans are strict: `"true"`, `1` and `"yes"` are rejected
rather than coerced.

None of it is stored, logged, echoed or counted. A test snapshots every table's
row count across a call carrying safety flags and asserts nothing moved.

---

## Why the hard handoff precedes release loading

Step 8A evaluates the handoff before it reads a profile or a formula, and that
ordering survives at the customer edge. When Step 8B returns
`HANDOFF_REQUIRED`, this endpoint evaluates the presentation with `release=None`
and never loads the active release at all.

Otherwise a corrupt activated release could suppress a pregnancy, breastfeeding,
medication, diagnosed-condition, child or under-12 hand-over — the one answer
this product owes unconditionally. The test replaces the release loader with one
that raises **if called**, so it proves the path is skipped rather than merely
survived.

The handoff message is Step 8F's own, byte for byte. Not paraphrased, not
replaced by a catalogue sentence, and carrying none of the flags that triggered
it.

---

## Why no active release means no verdict

With no active release the existing pure chain runs with `release=None` — the
same four functions, with three empty rule tuples passed in the open. Its
non-decision state is returned as-is.

No faked `WAIT`. No reading the Step 8I knowledge pack. No default policy. No
active production knowledge means no personal verdict, and that is a governed
state rather than a gap to paper over.

## Why a corrupt active release is not the same thing

`load_active_personal_decision_release` raising
`PersonalDecisionReleaseInvariantError` means a bundle is *activated and broken*.
Answering that as "no reviewed knowledge" would show customers a normal absence
while a corrupt release sits live, and nobody would be looking for it.

It fails closed: `503 FEATURE_UNAVAILABLE`, a generic message, no decision. The
manifest, the hash mismatch, the verification payload and the rule identities
stay server-side in the log.

---

## Why Step 8F remains the action authority

The only source of an action is `released.presentation.action`, and only when
Step 8F's status is `decision_presentable`. This layer never inspects signals,
claim strengths, source counts, semantic direction, policy identifiers or reason
wording to decide what to return. A static test rejects any
`supporting → buy` / `cautionary → skip` / `mixed → wait` mapping in Step 8K
code.

---

## Why copy resolution is a second gate

Step 8F proves a decision is **governed**: a reviewed rule, a reviewed reason, a
named openable source. It says nothing about whether the *sentence* a customer
reads has been reviewed. Those are two different approvals.

A release can be authored, approved and activated without anybody having written
the customer wording for its reason — release governance owns the rules and the
sources, not the prose. That must not be the moment a shopper is shown a
decision nobody proofread.

So `app/content/for_you_copy.py` is a second presentation gate, and it fails
closed. If either the verdict label or the reason sentence is missing, the
response is `not_enough_copy` with `action`, `verdict_key`, `verdict_text` **and
citation** all withheld.

**Why the citation goes too.** A source beside a hidden verdict still reveals
the shape of the claim we were about to make. `not_enough_copy` is a complete
block. The `release` identity is still reported, so the gap is traceable to the
exact bundle that needs wording.

`FOR_YOU_COPY_VERSION` travels in every response. A wording change increments
it, so a screenshot can always be traced to the text that was in force.

The copy module receives keys and resolves keys. It never sees a signal, a
claim, an ingredient, a policy or a profile — enforced by a static test that
asserts its only function parameters are `verdict_key` and `reason_key`, and
that it imports nothing at all.

---

## Why `knowledge_packs` is not imported at runtime

The active Step 8H release is the production knowledge authority. Step 8K
production code imports no knowledge pack; the reviewed petrolatum sentence
lives in the copy catalogue, and the **test** imports the pack to assert
`FOR_YOU_REASON_COPY[pack.REASON_KEY] == pack.FUTURE_REASON_INTENT`. That pins
the wording to the review without a runtime dependency.

## Step 8I remains inactive

No production evidence is authored, published, released, approved or activated
by this milestone. The three registries are still empty tuples, and an ordinary
reference seed still yields 0 Step 8I claims, 0 releases and 0 active releases.
Every active-release test builds its own release in a disposable database.

---

## What the endpoint does not do

- **It writes nothing.** No decision history, no analytics, no safety state. A
  test compares every table's row count before and after two calls.
- **It calls no model and no network.** Capture already happened; deciding is
  arithmetic over reviewed rows. A test replaces the gateway, the provider and
  the Open Food Facts client with functions that raise, and the endpoint still
  answers `buy`.
- **It uses no legacy care-verdict path.** No `purchase`, no `recommendation`,
  no appearance ROI. Static-tested.
- **It exposes no internals.** No semantic rule id, policy id, claim key, claim
  version, evidence strength, signal set, manifest, profile id or profile
  attribute id. The customer gets a decision, a reason and a source.
- **It adds no entitlement.** No payment, subscription or credit logic.

---

## Next

Step 8L wires this contract into the Product Result experience. The response
shape above is what it will render.
