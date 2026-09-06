# The FOR YOU decision release

Step 8H is the controlled way reviewed decision knowledge reaches production.
It does not author evidence, it does not decide anything, and — this is the
part most likely to be misread — **it ships no real knowledge**. After Step 8H
merges, production has zero active releases and therefore still emits no
BUY / WAIT / SKIP. The machinery exists; nothing has been put through it.

Step 8I is where the first real semantic rule, policy, explanation and release
are written and activated. That is a separate milestone on purpose: it is the
point where actual scientific and product judgement starts entering
production, and it must be reviewed as its own change.

## Why the three registries cannot be activated independently

Steps 8C, 8E and 8F each hold a registry:

- **8C semantic rules** map an exact published claim version to SUPPORTING or
  CAUTIONARY.
- **8E policy rules** map an exact governed state — category, the exact set of
  semantic rule identities, the direction set, three upstream gap flags — to
  BUY, WAIT or SKIP.
- **8F explanation rules** map an exact reviewed policy to one reason key and
  one exactly-chosen openable source.

They are one chain, and releasing any of them alone puts production into a
state nobody reviewed:

- A semantic rule with no policy changes what Step 8C matches while no
  reviewer has decided what should follow from it.
- A policy with no explanation can select an action that Step 8F will always
  withhold, because it has no reviewed sourced reason to show beside it. That
  is dead production knowledge: it looks live and can never speak.
- An explanation with no policy explains a decision that cannot be reached.

So the unit that is reviewed, approved, activated and retired is the whole
bundle. There are deliberately no per-rule database tables: rows a reviewer
can edit one at a time would make the reviewed thing and the activated thing
two different objects.

## The manifest

One immutable structured JSON document, schema version 1:

```json
{
  "schema_version": 1,
  "semantic_rules": [],
  "policy_rules": [],
  "explanation_rules": []
}
```

Every rule maps exactly onto the existing Step 8C, 8E and 8F dataclasses —
`PersonalDecisionSemanticRule`, `PersonalDecisionPolicyRule`,
`PersonalDecisionExplanationRule` — and is converted into those real classes
rather than into release-local copies. The three existing registry validators
(`build_rule_index`, `build_policy_index`, `build_explanation_index`) are then
called on the result, so Step 8H never restates what an internally consistent
registry means.

The schema is closed. Unknown keys, missing keys, wrong types, non-positive
versions and unrecognised enum values are all parse failures. A manifest is
read back out of a JSONB column and turned into the rules that decide what a
person is told, so "we did not recognise that field" must never quietly mean
"we ignored it".

Bounds: at most 512 rules of each kind and 1 MiB of canonical manifest. These
are not capacity limits reached by normal authoring; they are the point at
which a bundle has stopped being something a human reviewed line by line.

There is no `account_id`, `profile_id`, `scan_id`, medication, condition or
user text anywhere in the schema, and a guard refuses a document carrying one.
A release is global governed knowledge; whether it applies to a particular
person is decided at runtime by Step 8B against that person's own trusted
facts.

## Canonical form and the content hash

Meaning must not depend on the order an author happened to type things in, so
the manifest is canonicalised before it is stored or hashed:

- semantic rules sorted by `(rule_id, rule_version)`
- policy rules sorted by `(policy_id, policy_version)`
- each policy's semantic identities sorted by `(rule_id, rule_version)` — they
  are a `frozenset` and have no order of their own
- explanation rules sorted by `(explanation_id, explanation_version)`
- enums serialised by value; JSON with sorted keys, stable separators, UTF-8

`content_hash` is SHA-256 over that canonical JSON, stored as 64 lowercase hex
characters. Two manifests with identical contents and different input order
produce the same bytes and the same hash.

**The hash is an immutability guard, not a transport checksum.** It is
recomputed every time a manifest is read — at approval, at activation, and on
every runtime load. A stored manifest that no longer hashes to its stored hash
was edited outside the reviewed path, and the release is refused with
`PersonalDecisionReleaseInvariantError` rather than repaired. Repairing either
value would make the other one a lie.

## Review verification

A decision release is governed judgement, not merely a well-formed document.
Six named attestations are recorded on the release, and none of them is ever
inferred:

```
founder_review_completed
claude_review_completed
codex_review_completed
independent_reviews_agree
adversarial_review_passed
unresolved_doubt
```

Approval requires the first five true and `unresolved_doubt` false. Nothing in
this repository can observe that a founder read a rule or that two independent
reviews agreed, so absence is never consent: a partial or malformed block
reads as no attestation at all, not as the parts that happen to be present.

Recording verification does not approve anything. **Editing a draft clears it,
and so does cloning.** An attestation says a named person read *these* rules;
carrying it across a change would let new rules inherit the approval of rules
nobody compared them to.

## Cross-validation

### Semantic rule → published evidence

For every semantic rule, the exact `(claim_key, claim_version)` must resolve to
exactly one `EvidenceClaim` that is:

- `review_status = published`, and clears `claim_is_public_knowledge_path`
- not AI-generated
- `evidence_tier = clinically_studied`
- graded within `PERSONAL_APPLICABILITY_STRENGTHS`
- `claim_type = substance_personal_applicability`, `subject_type = substance`
- about the rule's exact `substance_key`
- in the evidence domain the Step 8B category mapping requires
- carrying a payload that parses and whose category matches the rule's
- carrying **at least one source path Step 8B would accept**

A rule pointing at missing, draft, approved-but-unpublished, rejected,
superseded, malformed, AI-generated, wrong-category or wrong-substance
evidence blocks both approval and activation.

The strength check imports `PERSONAL_APPLICABILITY_STRENGTHS` from Step 8B
rather than restating `strong` / `moderate` / `limited`. Two copies would
eventually disagree, and the direction of disagreement that matters is Step 8H
approving a release Step 8B will never project. It is a **membership test and
nothing else** — the only permitted shape is `in
PERSONAL_APPLICABILITY_STRENGTHS`, and a static guard rejects any other use:
comparing grades, ordering them, indexing a table by one, or passing one to a
function are all steps towards deriving a direction, an action, a weight or a
confidence from how strong the evidence looks. That is a judgement Step 8H has
no authority to make.

### Two source gates, and why they are not one

A semantic rule whose claim has **no** eligible source path is invisible to
Step 8B: it returns no claims for that substance at all, so the rule can never
match, and a policy keyed on its identity can never fire. The reviewed action
behind it is unreachable, and none of that is visible from the manifest.

That is a different question from the one the explanation asks. The
projectability gate asks *can Step 8B project this claim at all*; the citation
gate asks *which exact source did the reviewer choose to show*. A release whose
displayed citation is still perfectly valid can still rest on a second semantic
rule that no longer projects — and approving it on the strength of the citation
would approve a release that cannot work. Both gates read the same batch-loaded
path map, so neither costs a query.

**What this never does is read the science.** `summary`, `scope` and
`strength_rationale` are never read, and no direction is ever inferred from
them. A static test enforces that. SUPPORTING or CAUTIONARY is an explicit
reviewed Step 8C rule; the human judgement behind it is what the founder /
Claude / Codex / adversarial attestations govern. Step 8H validates provenance
and structure, and must never be able to substitute its own reading of a
paragraph for a reviewer's.

### Policy → semantics

Every `(rule_id, rule_version)` a policy references must be in the **same**
release. Never another release, never the static registry, never a different
version, never resolved implicitly. Every referenced semantic rule must carry
the policy's category.

The direction set is then derived independently from those exact semantics:

```
{}                      -> NONE
{supporting}            -> SUPPORTING_ONLY
{cautionary}            -> CAUTIONARY_ONLY
{supporting,cautionary} -> MIXED
```

and must equal the policy's declared `signal_set`. A mismatch fails the
release. The manifest is not repaired to match reality and reality is not
rewritten to match the manifest: a mismatch means the reviewed bundle is
inconsistent, and only a human can say which half is wrong.

This is equality checking, not inference. It answers "does this policy's
declared direction set match the semantics it names" and never "what should
follow from that direction set". A test pins the map to Step 8D's own so the
two cannot drift.

### No dead semantic rules

Every semantic rule in an approved or active release must be referenced by at
least one policy in the same release. Otherwise it changes what Step 8C
matches at runtime while no reviewed policy ever acknowledged its existence
(`UNREFERENCED_SEMANTIC_RULE`). Drafts may temporarily contain one.

### Explanation → policy, semantics and source

Every explanation must name an exact policy `(policy_id, policy_version)` in
the same release, and carry that policy's exact action. No matching by action
alone, no matching by policy id without version, and the action is never
inferred from the reason key.

Its `(semantic_rule_id, semantic_rule_version)` must be one of that policy's
own semantic identities, and its `substance_key`, `claim_key` and
`claim_version` must equal that semantic rule's exactly. No partial matching,
no fallback to another semantic rule, no first-match selection.

### Every policy is explained

Step 8F already refuses two explanations for one reviewed decision. Step 8H
adds the opposite requirement: **zero explanations for a policy blocks
approval** (`POLICY_EXPLANATION_MISSING`).

### Exact source anchoring

On the exact claim the explanation anchors to, there must be a source path
where `source.source_key` and `link.locator` both match the explanation
exactly — no normalisation, no trimming, no case folding. `"section 2"` and
`" section 2 "` are different reviewed anchors, and a locator's whitespace may
be part of how a reader finds the passage.

Nothing picks the first source, the strongest source, the newest source or the
official one. A reviewer chose one path; substituting another would attach a
reviewed sentence to evidence nobody reviewed it against. That exact path must
also pass `source_path_is_public_knowledge` against
`PERSONAL_APPLICABILITY_SOURCE_TYPES` — supporting relationship, reviewed
link, active source, allowed type, openable URL, non-blank licence note.

### Bounded queries

Cross-validation is two SQL statements whatever the release holds: one for
every claim the manifest names, one for every source path behind those claims.
A per-rule query would make a 500-rule release a thousand round trips, and a
validation step nobody is willing to run is a validation step that does not
exist.

## Lifecycle

```
DRAFT ──▶ APPROVED ──▶ ACTIVE ──▶ RETIRED
                          └──────────▲  (emergency deactivation)
```

There is no way back. `RETIRED → ACTIVE`, `APPROVED → DRAFT` and
`ACTIVE → DRAFT` do not exist. Reopening an approved release would mean
editing something a review already blessed; reactivating a retired one would
put knowledge into production without anyone checking it against today's
evidence. Both are done by **cloning into a new draft**, which copies the
exact manifest and drops verification, approval, activation and retirement —
the clone must earn its own.

Only a DRAFT is editable. Editing replaces the manifest, recanonicalises,
recomputes the hash and clears verification. There is no route by which a PUT
silently creates a new version of a reviewed release.

Approval is not activation. It records that this exact bundle was reviewed and
holds together; whether production should start using it is a separate
decision taken later.

## Atomic activation

Activation re-reads the persisted row before it touches anything: the schema
column, the manifest, the content hash, the recorded attestations, and then the
full evidence cross-validation. `status = approved` records that those checks
passed once; it does not prove the row still satisfies them, and every field
they read is editable in the database. The schema column matters separately
from the manifest's own internal `schema_version`, because the runtime loader
reads the column first — installing a release whose column disagrees would
create an outage the moment production asked for it, from an activation step
that only looked inside the JSON. Nothing is ever repaired: a column that
disagrees means the row was written outside the reviewed path, and quietly
correcting it would hide that. Approval applies the same schema-column check,
for the same reason.

The evidence cross-validation runs again here, and that repetition is the
point. Evidence moves between review and
activation: a published claim can be superseded by a new version, a source
retired, a URL removed, a licence note blanked, a claim's category changed. A
release that was coherent at approval may name evidence that no longer exists,
and activating it would put a citation in front of a customer that nobody can
open.

If any check fails, the candidate stays APPROVED and the current active
release stays ACTIVE. There is no half-switched state in which production has
neither.

When it succeeds, one transaction retires the old active release
(`retired_by`, `retired_at`) and activates the new one (`activated_by`,
`activated_at`), recording `supersedes_release_id` for audit. The retirement
is flushed before the activation: the single-active guard is a plain partial
unique index and is therefore checked per statement rather than at commit, so
writing the new ACTIVE row first would make a correct replacement fail on its
own invariant. Both statements are still in one transaction — only their order
within it has to be explicit.

## One active release, enforced by the database

```sql
CREATE UNIQUE INDEX uq_personal_decision_releases_active
ON personal_decision_releases (release_key)
WHERE status = 'active';
```

"At most one active release" cannot be an application convention. Two admins
pressing activate at the same moment both read one active release, both retire
it, and both insert their own. Activation therefore locks the whole series
with `SELECT … FOR UPDATE` in a deterministic order, and the index is the
backstop if the lock is ever lost.

If the runtime ever sees more than one active row it raises
`MULTIPLE_ACTIVE_DECISION_RELEASES` and returns nothing. It never picks the
highest version, the most recently activated, or the first row: production has
already been answering with knowledge nobody chose, and the honest response is
to stop rather than quietly settle it. Version numbers order releases and
decide nothing — version 7 is not preferred to version 6 anywhere.

## Emergency deactivation

`POST /admin/personal-decision-releases/{id}/deactivate` retires the active
release and activates nothing. **Zero active releases is a safe state**, not a
broken one: production falls back to no reviewed rules and therefore emits no
BUY / WAIT / SKIP, which is exactly what should happen when the reviewed
knowledge is in doubt.

The previous release is deliberately not revived. Rolling back is itself a
decision, made by cloning, reviewing and activating.

## The runtime loader

`load_active_personal_decision_release(session)` is one bounded query — there
are no per-rule child tables to join — and then in-memory parsing. It returns
`None` when nothing is active.

It re-checks the row on the way out of the database: status, supported schema
version, bounds, types, the three registry validators, and the content hash.
A JSONB column can be edited directly, so nothing here trusts that the API
wrote it. Any failure raises `PersonalDecisionReleaseInvariantError` and no
rules are returned.

What it does **not** do is re-run the evidence cross-validation. That pass
touches every claim and every source path a release names; doing it per
customer request would turn one scan into hundreds of queries to re-derive an
answer a human already signed off. Live evidence eligibility is still
guaranteed on every request — by Step 8B, which is the boundary that owns it.

The result is a frozen, slotted `ActivePersonalDecisionRelease` holding tuples.
No ORM row reaches the pure layers: they would then hold an object bound to a
session, and a lazy load inside a deterministic function is exactly the
surprise those layers exist to rule out.

## Why stale evidence fails closed by itself

Every semantic rule names an exact `claim_version`. When evidence is revised,
Step 8G publishes version 2 and supersedes version 1, so Step 8B starts
returning version 2 — which no rule in the old release matches. Step 8C
reports NOT_ENOUGH_DECISION_SEMANTICS, Step 8D's coverage is incomplete, Step
8E returns NOT_ENOUGH_INFORMATION and Step 8F shows nothing.

An active release therefore cannot silently inherit revised evidence. That is
a property of exact-version matching, not of a background job, and it is what
makes it safe for the runtime loader to skip the evidence pass.

## Why Steps 8C to 8F stay database-free

Their `rules=` parameters are explicit injection seams, and Step 8H uses
exactly those. None of them queries PostgreSQL, knows a release id, takes a
session, or reads a global. With no active release the orchestrator passes
`()` three times, in the open, so "production has no reviewed knowledge" is
visible in the call rather than hidden in a default.

The dependency direction is one-way and statically tested: Step 8H may import
Steps 8B to 8F; none of them may import Step 8H.

Safety is unaffected by any of this. Step 8A's hard handoff is carried through
Step 8C untouched and answered by Step 8E before any registry is consulted, so
a scan that needs a professional still says so when there is no decision
knowledge at all.

## Why no real release is seeded

Seeding a plausible-looking release would invent exactly the judgement this
milestone exists to withhold. `personal_decision_releases` is empty after
migration and after the ordinary reference-data seed, the three static
registries are still `()`, and there is no customer route to the released
evaluator — the orchestration seam exists so a later milestone can wire one
deliberately.

## Failure codes

Deterministic, so an admin sees which link broke rather than parsing prose.
They serialise in the existing `detail` shape as `reason`.

| Code | Meaning |
| --- | --- |
| `RELEASE_EMPTY` | approval needs at least one rule of each kind |
| `RELEASE_NOT_EDITABLE` | the release is past DRAFT, or the transition does not exist |
| `RELEASE_VERIFICATION_INCOMPLETE` | an attestation is missing or false |
| `RELEASE_UNRESOLVED_DOUBT` | doubt was recorded and left open |
| `RELEASE_CONTENT_HASH_MISMATCH` | the stored manifest was edited outside the reviewed path |
| `RELEASE_SCHEMA_VERSION_UNSUPPORTED` | the persisted schema column is not the supported version |
| `RELEASE_PERSONAL_DATA_PRESENT` | the document names a person, profile or scan |
| `EVIDENCE_CLAIM_NOT_PUBLISHED` | the exact claim version is missing or not published |
| `EVIDENCE_CLAIM_NOT_ELIGIBLE` | published, but fails the Step 8B eligibility boundary — wrong tier, wrong grade, unparseable payload, or no source path Step 8B would accept |
| `SEMANTIC_EVIDENCE_MISMATCH` | the claim is about a different substance, category or subject |
| `POLICY_SEMANTIC_NOT_IN_RELEASE` | a policy references semantics outside this bundle |
| `POLICY_CATEGORY_MISMATCH` | a policy references semantics from another category |
| `POLICY_SIGNAL_SET_MISMATCH` | the declared direction set is not what the semantics say |
| `UNREFERENCED_SEMANTIC_RULE` | a semantic rule no policy uses |
| `POLICY_EXPLANATION_MISSING` | a policy that could decide but could never be shown |
| `EXPLANATION_POLICY_NOT_IN_RELEASE` | an explanation for a policy outside this bundle |
| `EXPLANATION_ACTION_MISMATCH` | the explanation's action is not the policy's |
| `EXPLANATION_SEMANTIC_NOT_IN_POLICY` | the anchor is not one of the policy's semantics |
| `EXPLANATION_EVIDENCE_ANCHOR_MISMATCH` | the citation names a different evidence identity |
| `EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE` | the exact source path is missing or no longer eligible |
| `MULTIPLE_ACTIVE_DECISION_RELEASES` | corruption; the runtime refuses to choose |

## Next milestone

**Step 8I** writes and activates the first real release: real evidence through
Step 8G, a real semantic rule, a real policy, a real explanation, and a real
activation — plus the customer API and the FOR YOU screen that show the
result. That is where actual product and scientific judgement enters
production, and it is reviewed on its own.
