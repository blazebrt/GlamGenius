# Step 12B — the regulatory change authority

## The question this layer answers

One question, and nothing next to it:

> **What materially changed in official/regulatory authority for this exact product, in this exact pack context?**

It is answered deterministically, from official-source history we already hold, by
comparing two immutable observations of the same official record. It is not
answered by asking a model, by reading intent into a regulator's wording, or by
going and looking at anything.

The doctrine the whole layer is built to satisfy:

```
AI reads. Structured intelligence knows. Deterministic rules decide. AI explains.
```

No language model takes part in any decision here. §"No AI decides a regulatory
question" below says exactly which decisions are closed to it.

### What Step 12B is not

It does not alert anybody. It does not subscribe anybody to anything. It does not
poll FSSAI. It is not Product Watch. Those belong to Step 12C and none of their
machinery exists in this change — see §"No polling, no watch, no notification".

## Two authorities, not one: knowing and publishing

Step 12A learned this the expensive way; Step 12B is built with the lesson already
applied. Two separate authorities answer two separate questions, and the order
they run in is the design:

| | Module | Question | Runs |
| --- | --- | --- | --- |
| 1 | `backend/app/domains/official_records/change_projection.py` | Is the stored history valid, and what do the two observations state differently? | First, **always** |
| 2 | `backend/app/domains/official_records/change_evidence.py` | May a valid answer leave the server? | Second, separately |

`regulatory_change_for_record()` in `backend/app/domains/official_records/service.py`
asks them in that order. A claim is published only when both permit it.

Integrity alone is not permission to speak. Evidence alone can never make a
corrupt history publishable. And integrity is **never** skipped because the
answer was going to be withheld anyway: corrupt official history is a fact about
our storage that has to be found and logged whether or not a customer would ever
have seen it. `test_n2_integrity_runs_even_when_publication_is_withheld` holds
that order in place, and `test_n_evidence_can_never_override_integrity` forces
the gate to `True` over a corrupted ledger and proves nothing is published.

### Why the separation is not collapsible

The pipeline has five stages and they are deliberately distinct:

```
ingestion  →  immutable revision history  →  deterministic change projection
           →  evidence / publication gate  →  customer-visible change
```

Merging the projection into the gate would make "we cannot cite this" and "this
did not change" the same code path, and the product would eventually say the
second when only the first is true.

## Relationship to Step 12A

Step 12A is the **confirmed-label** change authority
(`docs/architecture/LABEL_CHANGE_AUTHORITY.md`): what changed on the physical
pack, from Store B label snapshots the customer's own scans produced.

Step 12B is the **official-record** change authority: what changed in what a
regulator published, from an operator-imported official artifact.

They share a shape and share no data:

- Both are an internal integrity authority plus a separate publication gate,
  integrity first and unconditional.
- Both fail closed to a governed envelope rather than to a guess.
- Neither feeds the other. A label change never implies an official change, and
  an official revision never touches a label snapshot, a label fingerprint or a
  `ScanDecisionEvent`.
- Both appear on Product Result as additive facts beside the scientific grade,
  never inside it.

## Relationship to Step 12C

Step 12C is awareness: watching, scheduling, subscribing, notifying. Step 12B is
derivation on read. The boundary is absolute and is enforced by a test, not only
by intent — see §"No polling, no watch, no notification".

The practical reading: Step 12B makes the answer *derivable and governed*, so
that Step 12C, when it is designed, has something correct to be built on rather
than a screen that invented a regulatory event.

## The ledger this reads: official revision authority

Nothing new is stored. Step 12B is a pure read over three tables that already
exist (`backend/app/domains/official_records/models.py`):

- **`OfficialSourceFetch`** — one accepted or refused look at the register.
  Carries `authority`, `record_type`, `source_url`, `adapter_version`,
  `source_checked_at` (the operator-supplied real download time),
  `status ∈ {succeeded, failed}`, `source_file_sha256`, `source_format`,
  `row_count`, `original_filename`, `error_code`.
- **`OfficialRecord`** — the canonical current row for one `external_record_id`,
  plus `first_seen_at`, `last_seen_at`, `last_seen_fetch_id` and
  `latest_revision`.
- **`OfficialRecordRevision`** — the immutable history. `record_id`,
  `source_fetch_id`, `revision_number` (unique per record), `observed_at`,
  `content_hash` and the full `payload` as parsed.

**No new table and no migration were required, and none was added.** The
immutable revision ledger already holds everything a deterministic comparison
needs; a `RegulatoryChangeEvent` table would have been a second, derived copy of
facts these rows already state, and would have had to be kept true forever.

### Revision integrity rules

Before any two revisions are compared, every one of these must hold. Each raises
`RegulatoryHistoryInvariantError` with a stable internal reason:

Per revision (`_check_revision`, prefixed `current_` or `previous_`):

| Reason | Means |
| --- | --- |
| `…_revision_belongs_to_another_record` | The row is not this record's |
| `…_revision_number_invalid` | Below 1 — no import path writes that |
| `…_revision_fetch_mismatch` | The supplied fetch is not the revision's own |
| `…_revision_fetch_unsuccessful` | Derived from a refused artifact |
| `…_revision_fetch_authority_mismatch` | Fetch authority/record type disagrees with the record |
| `…_revision_payload_invalid` | The payload is not a mapping |
| `…_revision_payload_schema_mismatch` | Payload keys are not exactly the canonical set |
| `…_revision_identity_mismatch` | Payload `external_record_id` is not the record's |
| `…_revision_content_hash_mismatch` | The stored hash no longer describes the stored payload |

Across the pair:

| Reason | Means |
| --- | --- |
| `canonical_record_disagrees_with_latest_revision` | See the next section |
| `current_revision_ahead_of_record` | A revision numbered beyond `latest_revision` |
| `first_revision_has_predecessor` | Revision 1 was handed a predecessor |
| `revision_predecessor_missing` | Revision ≥ 2 was handed none |
| `revision_sequence_non_contiguous` | The predecessor is not `current − 1` |
| `revision_observation_order_invalid` | The predecessor was observed after the current one |
| `adjacent_revisions_have_same_content` | Identical hashes; the importer never writes that |
| `revision_change_outside_material_fields` | Hashes differ with no material difference |
| `record_has_no_revision` | A canonical record with no history at all |

The hash check is the load-bearing one. It is the only thing standing between a
payload edited outside the import path and a customer-facing sentence about what
a regulator said, so the hash is **recomputed** under the importer's own rule
(`stable_content_hash`) rather than trusted.

**None of these is repaired on read.** A projection that quietly picked whichever
revision still parsed would publish a change nobody observed and hide the
corruption permanently. The failure is logged with its reason and the record's
authority and record type — never with a database identifier — and the customer
receives the same governed unavailable envelope they would receive for any other
reason. The official record itself, which was established without this envelope,
is still shown.

### The canonical-row / latest-revision invariant

`OfficialRecord` is a projection of its own latest `OfficialRecordRevision`. When
the compared current revision *is* `latest_revision`, every material field on the
canonical row must equal the same field in that revision's payload. If they
disagree, one of them was written outside the import path and there is no way to
tell which one is the register's word — so `canonical_record_disagrees_with_latest_revision`
fails closed rather than picking the more plausible-looking side.

Dates are compared after `isoformat()`, because the canonical row stores real
`date` objects and the payload stores the ISO text the importer serialised.

## The material-field whitelist

Only these eleven fields can constitute a regulatory change. They are exactly the
parsed official content in `source.canonical_row()`, minus `external_record_id`,
which is the key the record is filed under rather than a statement:

```
fbo_name, brand_name, product_name, licence, license_type, batch_lot,
reason, nature_of_recall, recall_status, recall_start_date, recall_termination_date
```

`test_material_fields_are_exactly_the_parsed_official_content` derives the set
from the parser, so adding a column to the source without deciding whether it is
a regulatory position fails a test rather than quietly becoming a customer-facing
"change".

These are **not** changes, and each for the same reason — it describes an
observation of ours, not a regulatory position:

- `created_at`, `updated_at`
- `last_seen_at`, `last_seen_fetch_id`, `first_seen_at`
- `source_file_sha256`, `adapter_version`, `original_filename`, `row_count`
- `observed_at`, `revision_number`, `content_hash`
- a repeated identical observation of the same content
- **absence from a later export**

## First-observation semantics

The first time we download a record is a fact about us, not about the regulator.
It does not mean FSSAI published it today, and it is never a change.

Revision 1 yields `status: "first_observed_record"` with no `changed_fields`, no
`changes`, and `previous_revision: null`. The vocabulary of a new regulatory
event — "new recall", "newly", "regulator did X" — appears nowhere in the values
the envelope states.

## Omission semantics

A record absent from a later export is not deleted, is not withdrawn, is not
resolved and is not cleared. The importer already holds that line; Step 12B does
not weaken it. Concretely, when a later valid export simply does not contain our
record:

- No revision is created for it. `latest_revision` stays where it was.
- Its canonical content is untouched.
- The projection still reads `first_observed_record` (or whatever the real
  history says), never a change, and never a clearance.
- `source_last_seen_at` and `seen_in_latest_successful_check` continue to carry
  the honest observation story, exactly as `FSSAI_OFFICIAL_RECORDS.md` describes.

`test_e_source_omission_invents_no_revision_and_no_clearance` also asserts that
the words *withdraw, cleared, resolved, safe, expired, cancelled* appear nowhere
in the resulting envelope.

## Source chronology

Source time only ever moves forward, and the importer enforces that on the way
in: an older `source_checked_at` is refused as `out_of_order_source_check`, an
equal one with the same digest as `duplicate_source_check`, an equal one with a
different digest as `conflicting_source_check` — the last of these fails closed
rather than picking a winner between two artifacts claiming the same instant.
The comparison itself runs under a transaction-scoped advisory lock so two
concurrent imports cannot both pass it.

Step 12B re-checks the consequence rather than assuming it: a pair whose
predecessor was observed *after* its successor was not written by that importer,
and is refused as `revision_observation_order_invalid`.

The projection itself has **no clock**. It is a pure function of two rows plus
their fetches: same pair, same answer, on any machine on any day. A test asserts
`datetime.now`, `utcnow`, `date.today` and `time.time` appear nowhere in it.

## Exact pack matching

Step 12B creates **no** second matching implementation. It reuses the existing
authority in `backend/app/domains/official_records/matching.py` exactly as it
stands, and attaches a change envelope only to records that `resolve_matches()`
already returned for this pack. In particular:

- Exact matching requires a valid 14-digit FSSAI licence **and** a meaningful
  exact batch/lot, on both sides, from a **confirmed Store B label snapshot**.
- Open Food Facts cannot supply a licence or a batch, so it cannot manufacture a
  match and therefore cannot manufacture a regulatory change (Store A stays
  behind the ODbL wall; see `docs/architecture/ODBL_DATA_WALL.md`).
- Brand and product only resolve or block ambiguity. They never establish a
  match on their own.
- No fuzzy matching. No AI matching.
- An ambiguous or unresolved candidate set publishes **nothing** — so there is
  no record for a change claim to be about, and the envelope does not exist.

## Revision-level matching

Pack attribution is about a *record*; a change is about *two revisions of that
record*, which need not describe the same pack. Four cases, and what each does:

- **A — both revisions describe the same pack.** `licence` and `batch_lot` are
  unchanged. The comparison is a straightforward statement of what else moved.
- **B — the previous revision matched, the identity then changed.** The later
  revision states a different licence or batch. This is reported as a change
  with `exact_identity_changed: true`, and it is **never** a clearance: "this
  record is now about a different batch" is not "your batch is fine". Whether
  the record still resolves to the pack at all is decided by the existing
  matching authority, on the canonical row, before any of this runs.
- **C — the latest revision matches but the previous did not.** The record only
  became attributable to this pack at the later revision. That is still
  `changed` with the fields that actually moved — it is explicitly *not*
  reported as "newly recalled", because we do not know when the regulator acted;
  we know when the record started naming this pack.
- **D — an ambiguous candidate set.** Nothing is published at all, per the
  matching authority above. No envelope, no partial answer.

The pair is always selected explicitly by the caller. `_revision_pair()` reads
`revision_number == record.latest_revision` and its predecessor
`revision_number − 1`, **on that record alone** — never by timestamp, never "the
nearest earlier row", never across records. `project_regulatory_change()` performs
no lookup of any kind and takes no session, so no vague identifier can quietly
re-point the comparison at a different revision.

## The evidence / publication boundary

The Product Constitution is unconditional: the app never makes a claim in its own
voice; it reports what a named, openable source says. **No source, no claim.**

A sentence like

> FSSAI changed the recall status from Initiated to Completed.

is **two** claims. It asserts what the register says now, and it asserts what the
register said before. Each needs its own openable official source. Publishing it
with evidence for only the current value would be citing today's page for
yesterday's words.

`regulatory_change_is_publishable()` therefore requires an openable official
source for **every** observation the claim rests on. A comparison with one
sourced side is not half-publishable; the sentence a customer reads is about
both.

`is_openable_official_source()` is deliberately strict, because the failure mode
is a claim that merely *looks* sourced: an absolute `http`/`https` URL with a
host, not pointing back into this application. A bare identifier, a file name, a
SHA-256, a relative path or a `file://` URL is not a source.

### Current limitation: historical openable provenance does not exist

Walk the provenance the importer actually records, and the answer today is "no"
for every pair:

- `OfficialRecord.source_url` and `OfficialSourceFetch.source_url` are both the
  module constant `SOURCE_URL` — `https://foscos.fssai.gov.in/food-recall`. One
  string, identical for every record and every revision ever written. It is a
  genuine, openable, external official page, and it proves exactly nothing about
  **which** revision said what: it shows whatever the register shows today. It
  may prove current authority; it cannot prove a previous historical value.
- `OfficialSourceFetch.source_file_sha256` is a digest of the downloaded
  workbook. **A SHA-256 is integrity metadata, not a locator.** It proves bytes
  did not change; nobody can open it.
- `original_filename` names a file on an operator's machine. It is not a URL, and
  the artifact itself is deliberately not retained — ingestion is manual and
  provenance-preserving, not an archive service.
- `OfficialRecordRevision.payload` is the transcription the claim is *derived
  from*. A claim cannot be its own independent support.

So **no stored field can locate a specific historical revision of the register**,
and `_REVISION_LOCATOR_FIELDS` is empty. `OfficialRecord.source_url` is
deliberately not listed in it: adding it would satisfy the gate while proving
nothing, which is precisely the failure the module exists to prevent.

The consequence is stated plainly: **Step 12B ships as a complete, tested
internal authority whose current customer-visible output is the governed
unavailable state, for every record.** That is the correct result, not a gap to
work around. No archive URL is fabricated, no private or internal artifact is
exposed, no file hash is dressed up as a URL, and the evidence rule is not
weakened to make the feature visible.

Restoring publication is a **provenance** change, reviewed on its own terms:
persist a revision-specific openable official locator — a durable public archive
URL for the exact artifact, or an official per-record permalink that exposes
history — and read it in `_REVISION_LOCATOR_FIELDS`. The publishing branch is
already implemented and tested
(`test_o_the_publishing_branch_works_when_both_authorities_permit_it`), so that
day is a small, reviewable diff rather than a rewrite.

## On the Product Result

The change is additive and lives under the **existing** official-record surface.
Nothing about the scientific verdict moves: `grade`, `band`, `decision.action`,
positives and negatives, nutrition components and alternatives are all untouched,
and `test_p_a_regulatory_revision_does_not_touch_the_scientific_verdict` proves a
revision changes none of them. Regulatory state is not part of the ASLI grade.

Each matched record in `official_records.records[]` gains one key:

```json
"regulatory_change": {
  "scope": "official_record_history",
  "status": "first_observed_record" | "changed" | "unavailable",
  "current_revision": 2,
  "previous_revision": 1,
  "changed_fields": ["recall_status"],
  "changes": [
    {"field": "recall_status", "previous_value": "Ongoing", "current_value": "Completed"}
  ],
  "exact_identity_changed": false
}
```

`scope` is part of the shape because this is the *official record's own* history,
not a statement about the packet in anybody's hand.

The five states §14 requires are distinguishable:

| State | How it appears |
| --- | --- |
| No relevant official record | `records` is `[]`; there is no `regulatory_change` key at all |
| First observed official record | `status: "first_observed_record"` |
| Valid internal revision change | `status: "changed"` with `changes` populated |
| Change unavailable / withheld | `status: "unavailable"` |
| Publishable change | `changed` / `first_observed_record` are emitted **only** when the gate permits |

An empty `changed_fields` is never used to assert "nothing changed": the
unavailable state is a distinct `status`, and `current_revision` and
`previous_revision` are `null` there, so it cannot be read as a comparison that
came back empty.

**Withheld and corrupt look identical from outside, by design.** A customer, and
anybody watching the response, cannot tell "we cannot cite this" from "our stored
history failed an invariant". The distinction lives only in the server log.

### Privacy and security of the envelope

This envelope is served to an anonymous device, so it carries no account id, no
device id, no `OfficialRecord` / `OfficialSourceFetch` / revision UUID, no media
id, no private storage URL, no AI run id, and no invariant reason.
`RegulatoryHistoryInvariantError` carries a fixed customer message and its reason
only as an attribute; `AppError.to_detail()` emits code, message and `retryable`,
and this error sets no `extra`, so no reason string can reach a response even if
the error were ever surfaced directly.

The withheld envelope also carries neither side of the comparison. Tests assert
the superseded value appears nowhere in the whole Product Result body.

## Historical Decision Memory and Shelf behaviour

Historical truth is not rewritten, and structurally cannot be:

- `ScanDecisionEvent` (`backend/app/domains/product/models.py`) pins the label
  snapshot, label version and content fingerprint that were true when the
  decision was made. It stores **no** official-record or recall state.
- Nothing under `backend/app/domains/purchase/` or
  `backend/app/domains/inventory/` stores official-record or recall state either.

So a new official revision has nothing to reach into. There is no backfill, no
mutation of past decision events, and no recomputation of stored history.
`test_q_a_later_revision_does_not_rewrite_historical_decision_memory` ingests a
revision and asserts the stored decision-memory and shelf rows are byte-identical
before and after.

Decision Memory and Shelf rows are **historical snapshots**. The official-record
envelope on Product Result is a **live projection**, recomputed per request. Step
12B adds only to the second kind.

## "Give things back" semantics

The architecture can represent a relieving change — a status moving to
`Completed`, a termination date appearing — because those are fields in the
material whitelist and a change in them is reported exactly as the register
states it.

What the architecture will never do is *infer* that a constraint lifted. It does
not conclude that a product is now safe, that a recall is over, that a customer
may now consume something, or that a regulator has relaxed its position. It
reports the field that moved, in the register's own words, and stops. An empty
`recall_termination_date` becoming a date is a fact about a record; it is not
permission.

## No AI decides a regulatory question

No model runs in `change_projection.py` or `change_evidence.py`, and no model
output can reach either. Closed to AI, permanently:

- whether a regulator changed position;
- whether a recall was strengthened or weakened;
- whether a product is now safe;
- whether an official record applies to a pack;
- whether two official revisions mean the same thing;
- whether an official change is good or bad;
- whether the customer should consume or use something.

AI's only legitimate role anywhere near this data is reading a label into
structured facts, upstream, and explaining an already-decided deterministic
result in plain words. It decides nothing here.

## No polling, no watch, no notification

Step 12B derives a change **on read**, from a ledger an operator already
imported. It creates no `RegulatoryChangeEvent` table, no `ProductWatch`, no
`RegulatoryWatch`, no subscription, no queue, no schedule, no cron job, no
polling job, no notification row, and no new worker behaviour. No existing worker
was touched, and the notification worker cannot reach this authority: it compiles
Today, and nothing in that path calls `regulatory_change_for_record()`.

There is also no automatic FSSAI acquisition of any kind — no scraping, no
CAPTCHA bypass, no private endpoint, no paid API, no browser automation. Official
data still enters only through the manual, provenance-preserving import path.

This is enforced, not merely intended.
`test_no_step_12c_behaviour_is_reachable_from_this_authority` parses both modules
and inspects every name, attribute, argument, import and string literal the code
actually uses — docstrings excluded, so the modules can say Step 12C is absent
without that promise reading as a breach — and fails if any of them contains
`notify`, `notification`, `subscribe`, `subscription`, `watch`, `alert`, `cron`,
`schedule`, `poll`, `httpx`, `requests`, `celery`, `queue` or `worker`. A second
test proves the guard actually fires on a module that does those things.

`fetch` is deliberately not on that list: the existing ingestion ledger is spelled
`OfficialSourceFetch`, and reading a row an operator already imported is the whole
of Step 12B. Going and getting one is what Step 12C would have to do, and that is
what the rest of the list bans.

## Failure modes

| Situation | What happens |
| --- | --- |
| Record has no revision at all | Logged `record_has_no_revision`; unavailable envelope |
| Any revision integrity invariant fails | Logged with its reason; unavailable envelope; the official record is still shown |
| A revision's `OfficialSourceFetch` row is missing | Logged `revision_fetch_missing`; unavailable envelope |
| History is valid but no openable source backs it | Unavailable envelope, silently — this is the expected state today, not an error |
| Candidate set ambiguous or unresolved | No record is published, so no envelope exists |
| Pack facts unconfirmed | Matching does not run; `records` is `[]` |

Nothing here takes the page down. The official-record section predates this
envelope and renders without it; a broken invariant removes the comparison, not
the record.

Log lines carry `authority`, `record_type` and `reason` only. No record id, no
revision id, no fetch id, no payload content.

## Customer-visible surface today

None changed. The envelope is additive on the API and every record currently
reports `status: "unavailable"`, so no new sentence is shown to anyone.

The frontend's official-record type
(`frontend/src/services/verdictModel.ts`) already carries an index signature, so
the new key type-checks and is ignored by `OfficialRecords.tsx`. **No frontend
code, no new keyed string and no frontend test was added**, because there is
currently nothing legitimately publishable to render. Adding copy for a state the
evidence gate never reaches would be writing a claim before its source exists.

When a revision-specific locator is persisted, the rendering work becomes a real
task: neutral, factual, keyed strings under the existing Official Records area,
governed by `LEGAL_RULES.md` — of the shape

```
Official FSSAI record changed
Recall status: Completed
Previously recorded: Ongoing
```

and never a new tab, a Product Watch screen, an alerts screen, a danger badge,
red alarm UI, or any of "This product is safe now", "The danger is over", "FSSAI
cleared this product", "This formula is dangerous", "Government warning", "You
should avoid this product".

## Rollback

Step 12B is additive and reversible with no data consequences, because it wrote
no data:

1. Revert the pull request. There is **no migration** and **no schema change**,
   so nothing has to be undone in any database.
2. `backend/app/domains/official_records/change_projection.py` and
   `change_evidence.py` are new files with no other callers; deleting them plus
   the three additions in `service.py` returns
   `recalls_for_pack()` to its previous output exactly.
3. The only observable API difference is the extra `regulatory_change` key on
   matched official records. Removing it restores the previous response shape
   byte for byte; no client depends on it, because nothing renders it.
4. No worker, no schedule, no external integration and no stored state is
   involved, so there is nothing to drain, disable or clean up.

## The pre-implementation audit, answered from code

1. **What immutable data proves that an official record changed?**
   `OfficialRecordRevision` — one immutable row per accepted content change, with
   `revision_number`, `observed_at`, `content_hash` and the full parsed
   `payload`. An unchanged re-observation advances `last_seen_at` and
   `last_seen_fetch_id` and writes no revision, so the existence of revision *n+1*
   is itself the proof that content changed.

2. **What is the exact predecessor of a revision?**
   The row for the same `record_id` with `revision_number − 1`. Nothing else.
   `(record_id, revision_number)` is unique, so the predecessor is exact and
   singular. It is never inferred from `observed_at`, from the nearest earlier
   row, or from another record.

3. **How is the canonical `OfficialRecord` related to its latest revision?**
   It is a projection of it: `latest_revision` names the revision and every
   material column must equal that revision's payload. Disagreement is corruption
   and fails closed.

4. **Which fields are semantic source content versus observation metadata?**
   The eleven fields of `canonical_row()` minus `external_record_id` are source
   content. Everything else the schema holds — timestamps, fetch ids, digests,
   adapter version, filename, row count, revision number, content hash — is our
   bookkeeping about when we looked. See §"The material-field whitelist".

5. **Which source locator is actually openable by a customer?**
   Only `SOURCE_URL` (`https://foscos.fssai.gov.in/food-recall`), a single
   module constant shared by every record and every revision. Nothing else stored
   is openable at all.

6. **Can the previous revision's content currently be independently opened from
   that locator?**
   **No.** One constant register page cannot evidence a specific historical
   value; the workbook is not retained; the digest is not a locator. This is why
   the gate returns `False` for every pair today.

7. **Which pack facts establish exact product applicability?**
   A confirmed Store B label snapshot's 14-digit FSSAI licence and a meaningful
   exact batch/lot, both required, both normalised by the existing
   `normalise_licence` / `normalise_batch`. Brand and product resolve or block
   ambiguity only.

8. **Can a change be attributed to the exact pack without using OFF?**
   Yes, and only that way. OFF holds no licence and no batch, so it cannot
   contribute to matching at all. The ODbL wall is untouched: Step 12B reads
   Store B exclusively and creates no cross-store join, table, view or cache.

9. **What happens when an old revision matched but the latest changes
   licence/batch/product identity?**
   Case B above. The identity change is reported as a fact with
   `exact_identity_changed: true`; whether the record still resolves to the pack
   is decided by the existing matching authority on the canonical row; and it is
   never converted into a clearance.

10. **What Decision Memory / Shelf records are historical snapshots versus live
    projections?**
    `ScanDecisionEvent` and the shelf/inventory links are historical snapshots,
    pinned to the label snapshot, label version and fingerprint of the moment.
    The Product Result official-record envelope is a live projection recomputed
    per request. Step 12B touches only the latter.

11. **Could introducing 12B accidentally rewrite historical truth?**
    No. It performs no write of any kind — no insert, no update, no backfill —
    and the snapshot tables store no official-record state for it to reach. Test
    Q proves the stored rows are unchanged across a revision.

12. **Could a notification worker accidentally turn this into 12C behaviour?**
    Not without a deliberate new call. The notification worker compiles Today
    through the same compiler `GET /api/v2/today` uses; nothing on that path
    reaches `official_records`. No worker file was touched, and the vocabulary
    guard above fails if this authority ever grows a word that belongs to Step
    12C.

## Operational note carried forward from Step 12A

Migration `j8k9l0m1n2_step12a_boundary_aware_label_identity.py` was corrected in
place during Step 12A. That correction protects databases that had **not yet**
executed the defective earlier version. **We have not established whether any
persistent database executed the old defective version before the correction.**

Therefore, and explicitly within this work: no claim is made that historical
persistent data was repaired; no production query was run; no connection to
production Supabase was made; no repair migration was added; and this operational
question was not mixed into Step 12B. It remains a separately tracked
owner/operations question.

## Deliberately out of scope

Product Watch, alerts, push, email, in-app notices, alert queues, scheduling,
cron, polling, background fetch, watcher workers, subscription tables — all Step
12C. Automatic FSSAI acquisition of any kind. Any change to the scientific grade,
decision action or alternatives. Any new persistence. Any paid resource.

## See also

- `docs/architecture/FSSAI_OFFICIAL_RECORDS.md` — the ingestion, chronology and
  matching authority this layer reads
- `docs/architecture/LABEL_CHANGE_AUTHORITY.md` — Step 12A, the same shape for
  confirmed pack labels
- `docs/architecture/ODBL_DATA_WALL.md` — why Open Food Facts cannot contribute
  to any of this
- `LEGAL_RULES.md` — how any future customer-visible string here must be written
