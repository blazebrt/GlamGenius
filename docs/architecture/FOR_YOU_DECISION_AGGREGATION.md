# FOR YOU decision aggregation foundation

## Step 8D boundary

Step 8D answers one narrow question:

> Given one exact Step 8C result, which distinct reviewed rules are
> represented, which reviewed directions are present among them, and how much
> of the claim output Step 8C actually produced carries a reviewed mapping?

It does not answer what the customer should do about the product. There is no
score, weight, rank, confidence number, conflict resolution, recommendation or
`BUY` / `WAIT` / `SKIP`.

The governed chain now reads:

```text
LabelSnapshot
    ↓
Step 7B / 7B.1 — exact formula
    ↓
Step 7A — canonical identity
    ↓
Step 7C — category-specific reference evidence
    ↓
Step 8A — trusted personal context
    ↓
Step 8B — governed personal evidence applicability
    ↓
Step 8C — exact claim version → reviewed semantic direction
    ↓
Step 8D — deterministic signal aggregation
    ↓
future governed product-decision policy
    ↓
future BUY / WAIT / SKIP
```

Step 8D's only input is a `LabelSnapshotPersonalDecisionSemantics` that Step 8C
already produced. It takes no `AsyncSession`, no `account_id`, no
`LabelSnapshot`, no category argument, no safety input and no rules argument —
the public signature is pinned to a single parameter by a test. It never calls
`build_rule_index` or `project_personal_decision_semantics`: it consumes a
result, it does not create one.

`personal_decision_semantics` is its sole application-domain dependency. A
static test asserts the exact set of `app.domains.*` imports, so reaching
around Step 8C into `personal_applicability`, `personal_lens`, `profile`,
`evidence`, `product`, `substance_interpretation` or any other domain fails
immediately.

## The distinction that matters most

**Semantic mapping coverage is not product completeness.**

`PersonalSemanticMappingCoverage` describes coverage over the claim
projections Step 8C emitted, and nothing else. `COMPLETE_SEMANTIC_MAPPING` is
the value most likely to be misread. It means only:

> every claim projection Step 8C actually produced has an explicit reviewed
> semantic mapping.

It does **not** mean the formula was completely read, that every ingredient
identity resolved, that enough Step 7C evidence exists, that enough personal
evidence exists, that the product is suitable, or that any product-level
statement is permitted.

The values are named structurally on purpose. None of them is called
`product_complete`, `decision_ready`, `enough_evidence` or `no_evidence`,
because each of those would be a claim about the product that this layer
cannot support:

| Value | What it means, exactly |
| --- | --- |
| `NO_CLAIM_PROJECTIONS` | Step 8C emitted zero claim projections |
| `NO_MAPPED_SEMANTICS` | Projections exist; every one is unmapped |
| `PARTIAL_SEMANTIC_MAPPING` | At least one mapped and at least one unmapped |
| `COMPLETE_SEMANTIC_MAPPING` | Projections exist; every one is mapped |

`NO_CLAIM_PROJECTIONS` in particular can arise from a hard handoff,
insufficient personal context, no applicable Step 8B evidence, unresolved or
ambiguous identity, or a formula path with no applicable claims. Step 8D does
not guess which. Handoff and plain absence are deliberately indistinguishable
from the structure alone — a test pins that — and the reason stays reachable
through the preserved source object.

## The whole upstream result is preserved

`PersonalDecisionAggregation.source_semantics` is the exact object that was
passed in — the same instance, asserted with `is`, not a copy, a rebuild or a
serialization round trip.

That is deliberate. It keeps provenance, category, formula status, profile ID
and version, context status, handoff, ingredient identity states, ambiguity
candidates and every original Step 8C projection reachable, without Step 8D
importing the domains that own them and without flattening them into a
lossy summary. A later governed policy layer must read that object to decide
whether any product-level action may be emitted; Step 8D's structural summary
is not a substitute for it.

Step 8D does not interpret those fields. It carries them.

## Set membership, never voting

The direction summary is computed from the set of directions present among
**distinct** rules:

```text
{}                          → NONE
{SUPPORTING}                → SUPPORTING_ONLY
{CAUTIONARY}                → CAUTIONARY_ONLY
{SUPPORTING, CAUTIONARY}    → MIXED
```

That is the entire algorithm — a lookup on a frozen set, with nowhere for a
tie-break to be inserted. There is no tally, majority, ratio, net, average,
threshold or percentage anywhere in the layer, and static tests reject both
the vocabulary and any ordering comparison (`<`, `<=`, `>`, `>=`) in
production code, because a vote needs one somewhere.

**Ten distinct SUPPORTING rules and one distinct CAUTIONARY rule is `MIXED`** —
the same answer as one and one. Tests pin both directions of that asymmetry
explicitly, because 1 + 1 alone would not catch a majority rule creeping in.

The reason is not squeamishness. The number of published claims about a
substance reflects how much research happened to be done and how many rules
happened to be reviewed. It is not how strongly anything acts on a person.
Counting them would manufacture a magnitude out of research volume.

`MIXED` means both reviewed directions are represented. It does not mean
balanced, cancelled, equivalent, resolved, inconclusive, or that the customer
should hesitate. There is no winner, and producing one is a later reviewed
layer's job.

## Repeated occurrence is provenance, not weight

A rule's identity is exactly `(rule_id, rule_version)`.

One reviewed rule matching the same substance at printed positions 0, 3 and 8
produces **one** `AggregatedPersonalDecisionRule` carrying **three**
`PersonalDecisionSignalOccurrence` entries. The signal set and the mapping
coverage are identical to the single-occurrence case; a test compares them
directly. Occurrences exist so a later layer can say where a rule came from,
never to make a direction count for more.

Ingredient position is carried in occurrence provenance and never read as
concentration, dose or importance. Changing positions from `0, 1, 2` to
`10, 50, 100` leaves the signal set and coverage untouched.

Two versions of one rule id are two distinct rules. `rule.a@1` SUPPORTING
alongside `rule.a@2` CAUTIONARY is two rules and `MIXED`. Step 8D has no
recency policy and never decides that a later version supersedes an earlier
one.

## Fail closed on impossible input

Step 8C's frozen dataclasses already reject most malformed projections, so
reaching `PersonalDecisionAggregationInvariantError` means the object was
assembled or mutated outside that path. Aggregating it anyway would launder
corrupted provenance into something that reads as reviewed, so it raises:

- a mapped projection missing its rule id, rule version or direction;
- a direction outside the reviewed vocabulary;
- a projection on an ingredient with no resolved substance key;
- an unmapped projection carrying rule provenance;
- an unrecognised semantic status;
- one rule identity carrying two different directions — this is **not**
  `MIXED`; `MIXED` is only ever different valid rule identities disagreeing;
- one rule identity pointing at two different evidence targets
  (`substance_key`, `claim_key`, `claim_version`). Category is not part of the
  comparison because one Step 8C result carries exactly one.

Occurrences of a corrupted identity are never merged. A reviewed rule identity
cannot silently change what it targets downstream.

## Missing mappings stay visible

Every unmapped claim projection becomes an `UnmappedPersonalDecisionClaim`
with exact provenance — ingredient position, substance key, claim id, claim
key, claim version — and nothing else. Claim prose, scope, evidence strength,
evidence tier, sources and matched facts are absent, and a static test rejects
reading any of them: Step 8C already converted reviewed mappings into
controlled direction, so Step 8D has no legitimate reason to see them.

There is no fallback and no default direction. A gap in reviewed mappings is a
fact about what has been reviewed, and hiding it would let a partial picture
read as a whole one.

## Deterministic and pure

Distinct rules are returned in the order their identity is first encountered
during ingredient-then-claim traversal; occurrences and unmapped claims in
encounter order. Nothing is sorted alphabetically, ranked or reordered.
Reordering the input claims leaves the signal set and coverage unchanged while
the rule order follows the new encounter order — a test pins both halves.

Step 8D performs 0 SQL queries, 0 inserts, updates, deletes, flushes or
commits, 0 network calls, 0 AI calls, 0 Open Food Facts reads and 0 filesystem
knowledge loading. Every public function is synchronous. It adds no migration,
table, model, column, index, evidence claim type, cache, persistence, seed,
API route or frontend surface; the Alembic head is unchanged at `b8c9d0e1f2`.

Every public result dataclass is `frozen=True, slots=True` and every returned
collection is a tuple. The only contained upstream object is the already-frozen
Step 8C result.

## Red lines

- **Set membership, never voting.** 10 SUPPORTING + 1 CAUTIONARY is `MIXED`.
- **Repeated occurrence is not extra weight.** One rule seen three times stays
  one distinct rule.
- **Complete semantic mapping is not complete product evidence.** It means
  only that every projection Step 8C produced is mapped.
- **Missing mappings stay visible.** No fallback, no default direction.
- **`MIXED` remains unresolved.** No winner.
- **No product verdict.** No `BUY` / `WAIT` / `SKIP`, and no score.
- **No production knowledge.** Step 8D contains zero real substance or
  ingredient rules; tests use clearly synthetic identities only.
