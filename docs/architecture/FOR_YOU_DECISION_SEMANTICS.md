# FOR YOU decision semantics foundation

## Step 8C boundary

Step 8C answers one narrow question:

> Given a claim that Step 8B has already accepted as applicable to this user,
> has a reviewer explicitly decided which direction that exact claim version is
> permitted to contribute in?

The answer is either a reviewed direction — `supporting` or `cautionary` — or
`not_enough_decision_semantics`. There is no third possibility, and the second
one is the default.

It produces a live, immutable read projection. It is not a score, a grade, a
weight, a confidence number, a ranking, a safety classification, a
recommendation, an alternative, or a `BUY` / `WAIT` / `SKIP` verdict. No
arithmetic of any kind is performed on evidence.

The intended layering is:

```text
LabelSnapshot
    ↓
Step 7B / 7B.1 — formula and canonical identity
    ↓
Step 7C — category-specific published reference knowledge
    ↓
Step 8A — trusted personal context
    ↓
Step 8B — personal evidence applicability
    ↓
Step 8C — reviewed decision direction per exact claim version
    ↓
Step 8D — future governed aggregation
    ↓
future product decision
```

Step 8C consumes exactly one input: a `LabelSnapshotPersonalApplicability`
value that Step 8B has already produced. It takes no `AsyncSession`, no
`account_id`, no `LabelSnapshot`, no barcode, no safety input and no free text.
Everything it is allowed to know has already been decided by a reviewed layer
above it.

## Why the production registry is empty

`PERSONAL_DECISION_SEMANTIC_RULES` in
`backend/app/domains/personal_decision_semantics/rules.py` is an empty tuple in
V1, and that is the design rather than an unfinished part of it.

A published claim states what a source found. Which direction that finding is
permitted to push a product decision is a *separate* judgement, about this
product, this category and this user population — and nobody has made it yet
for any claim. Seeding plausible-looking rules so the feature appears to
"work" would manufacture exactly the judgement this milestone exists to
withhold, and it would do so invisibly, inside a data structure that no
reviewer signed.

So the honest V1 output for every real claim is
`not_enough_decision_semantics`. The machinery for the reviewed case is built,
tested and fails closed; the reviewed cases themselves arrive later, one at a
time, each as its own reviewed change.

Tests inject synthetic rules through the keyword-only `rules=` parameter of
`project_personal_decision_semantics`. That seam exists so the matching
behaviour can be proven without a single production rule existing.

## Direction comes from a rule, never from the claim

A rule is keyed to a complete evidence identity — all four parts:

```text
(category, substance_key, claim_key, claim_version)
```

Matching on a subset is prohibited, and the four-part key is what enforces it.
A claim revised after review gets a new `claim_version`, and the old review's
direction does not follow it: the revised claim returns
`not_enough_decision_semantics` until a reviewer looks at the new text.
`packaged_food` never inherits a `skin_care` rule, and `cosmetics` is never
silently treated as `skin_care`.

Everything else that *could* be read as a direction is deliberately not read:

- **Claim prose.** `summary` and `scope` are never touched. A claim that reads
  reassuringly still carries a `cautionary` direction if that is what the rule
  says, and a claim that reads alarmingly still carries `supporting`. A static
  AST test asserts that the service module contains no `.summary` or `.scope`
  access at all.
- **Evidence strength and tier.** `STRONG`, `MODERATE` and `LIMITED` describe
  how well a finding is established. They are not points, and they never
  become `+3` / `+2` / `+1` or any equivalent. The same claim under any
  strength yields the same signal.
- **Sources and matched facts.** Neither the number of sources nor which
  Profile facts matched changes the direction. Step 8B already decided
  applicability; Step 8C does not re-decide it.
- **Ingredient position.** Position is carried through unchanged as printed
  order. It is not concentration, not dose, not importance, and not a weight.

## Fail-closed everywhere

- An applicable claim with no reviewed rule contributes nothing, rather than
  being guessed at.
- A registry with two rules aimed at the same evidence identity is rejected
  outright. It is not resolved by recency, by declaration order, or by a
  "safer" preference for `cautionary` — picking a winner there would be an
  unreviewed policy decision made by a tie-break. `build_rule_index` raises
  `PersonalDecisionSemanticRegistryError` and nothing is projected.
- Blank identifiers, non-integer or non-positive claim versions, and
  non-enumeration categories or signals are rejected the same way.
- An unresolved identity gets no semantics. An ambiguous identity gets no
  semantics either, even when a candidate substance happens to have a rule:
  personal decision semantics may not break an identity tie.
- A Step 8A hard handoff is carried through with zero ingredients and zero rule
  lookups. Step 8A owns the medical boundary; Step 8C only obeys it.
- `ClaimDecisionSemanticProjection` validates in `__post_init__` that status and
  rule provenance cannot disagree: `semantics_available` requires a `rule_id`,
  `rule_version` and `signal`, and `not_enough_decision_semantics` requires all
  three to be absent.

## No aggregation — that is Step 8D

Opposing signals on the same ingredient, or on the same product, are returned
independently and unchanged. Step 8C does not count them, does not compare
their number, does not decide which side wins, does not deduplicate, and does
not reorder. Duplicate printed ingredients remain duplicate output rows.
Ingredient order and claim order are preserved exactly as Step 8B produced
them.

Combining these directions into anything a customer would recognise as an
answer is Step 8D's reviewed job, and it does not exist yet. A static test
rejects counting and aggregation identifiers (`total`, `sum`, `count`,
`aggregate`, `weight`, `score`, and the like) anywhere in the production
modules.

## Pure, and provably so

Step 8C performs 0 SQL queries, 0 inserts, updates, deletes, flushes or
commits, 0 network calls, 0 AI calls, 0 Open Food Facts reads and 0 filesystem
knowledge loading. Every public function is synchronous.

It adds no migration, table, column, index, evidence claim type, cache, result
persistence or production seed. The Alembic head is unchanged at `b8c9d0e1f2`.
It exposes no API route and no frontend surface.

Static AST tests in `backend/tests/test_step8c_personal_decision_semantics.py`
hold this up rather than trusting convention: the production modules may import
`app.domains.personal_applicability` and nothing else from `app.domains`, and
may not import SQLAlchemy, `httpx`, `requests`, `google`, or the shared
database package.

The pass-through metadata fields — `provenance`, `context_status`, `handoff`
and `identity_status` — are annotated as `object` for that reason. Those values
belong to Step 8A, Step 7C and the product domain, and Step 8C carries them
without inspecting them. Typing them loosely costs some type fidelity and buys
an import boundary with no exceptions in it.

## The contract above it is unchanged

Step 8C adds a sibling domain. It does not modify the approved Step 8B
contract: no Step 8B dataclass, enum, status value or function signature
changes, and Step 8B's own tests are untouched.
