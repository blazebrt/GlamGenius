# FOR YOU decision policy foundation

## Step 8E boundary

Step 8E answers one narrow question:

> Given one exact Step 8D aggregation, is a personalized decision structurally
> permitted at all, and has a reviewer explicitly decided an action for this
> exact governed state?

It does not answer how the action should be explained to anyone. That is
Step 8F, and no user-facing sentence belongs here.

The governed chain now reads:

```text
LabelSnapshot
    ↓
Step 7B / 7B.1 — exact formula
    ↓
Step 7A — canonical identity
    ↓
Step 7C — reviewed category evidence
    ↓
Step 8A — trusted personal context
    ↓
Step 8B — applicable personal evidence
    ↓
Step 8C — exact claim version → reviewed semantic direction
    ↓
Step 8D — exact distinct rule aggregation
    ↓
Step 8E — explicit versioned decision policy
    ↓
Step 8F — future user-facing verdict and reason contract
```

The machine-readable action vocabulary — `buy`, `wait`, `skip` — enters the
backend here. **The production registry is empty, so production emits none of
them.** The architecture for carrying a reviewed action exists; no reviewed
instance of one does.

## WAIT is not uncertainty

This is the distinction the whole milestone is built around.

| | Meaning |
| --- | --- |
| `WAIT` | A reviewer looked at this exact governed state and decided waiting is the right product action |
| `NOT_ENOUGH_INFORMATION` | The system was not *entitled* to make a product decision |

Converting the second into the first is the most tempting mistake available in
this layer and the most damaging, because it dresses a gap in our own
knowledge up as advice and the customer cannot tell the difference. Missing
personal context is not a reason to wait. A formula that failed to parse is
not a reason to wait. An unmapped claim is not a reason to wait. No policy
existing is not a reason to wait.

So no gate in Step 8E produces an action of any kind. Every blocked path
returns an explicit non-decision with a structural reason, and tests inject a
synthetic `WAIT` policy on each blocked path to prove the registry is never
even consulted.

Three negative statuses stay distinct rather than collapsing into one, because
a later layer must handle them completely differently:

- `HANDOFF_REQUIRED` — a professional should be involved.
- `NOT_ENOUGH_INFORMATION` — we may not decide.
- `NOT_ENOUGH_DECISION_POLICY` — we could decide, but nobody has reviewed this.

## Evaluation order

```text
1. hard handoff            → HANDOFF_REQUIRED
2. category conversion     (unknown → invariant error)
3. personal context        → NOT_ENOUGH_INFORMATION / PERSONAL_CONTEXT_NOT_COMPLETE
4. formula projectability  → NOT_ENOUGH_INFORMATION / FORMULA_NOT_PROJECTABLE
5. Step 8D structure + mapping completeness
                           → NOT_ENOUGH_INFORMATION / SEMANTIC_MAPPING_NOT_COMPLETE
6. derive the exact policy target
7. build + validate the registry, then look up exactly
```

**Handoff is evaluated before the registry is even built.** A conflicting or
malformed policy registry must never be able to turn a safety handoff into an
error, so the handoff path performs zero registry work, inspects no formula,
no ingredients and no semantic rules. A test injects a registry that cannot
build and asserts the handoff still comes back cleanly.

Registry construction sits last for a quieter reason: a registry problem
should not surface as noise on a request where no decision was structurally
permitted anyway.

Step 8E does not call `hard_handoff` and does not import `personal_lens`. The
upstream Step 8A decision is authoritative, and re-deriving a medical boundary
in a second place is how two places start to disagree.

## Only complete personal context may reach policy

`context_available` proceeds. `partial_context` and
`not_enough_personal_context` return `NOT_ENOUGH_INFORMATION`.

Step 8B selects personal evidence from the trusted body facts that happen to
be present. A partial context can therefore hide an applicable evidence path
entirely — the claim that would have mattered was never eligible to match. A
personalised product action taken on that basis would be pretending to know
the person better than we do.

**Packaged food consequence.** Step 8A has no V1 packaged-food body-fact
allowlist, so packaged-food FOR YOU paths naturally fail this gate today. That
is not special-cased here and must not be: inventing nutrition health facts to
get past it would be exactly the unreviewed judgement this chain exists to
withhold. Product Truth remains a separate surface, and expanding personal
context is deliberate future health-mode work.

## Only a parsed formula, and only complete semantic mapping

A formula status other than `parsed` — `empty`, `malformed`,
`ambiguous_boundary`, `too_long`, `too_many_items`, or absent — returns
`FORMULA_NOT_PROJECTABLE`. A parser failure is a gap in what we read off the
label, never a product judgement, and never `SKIP`.

Only `COMPLETE_SEMANTIC_MAPPING` reaches policy lookup. As Step 8D's own
documentation stresses, that value means only that every claim projection
Step 8C emitted carries a reviewed mapping. It does **not** mean the product
evidence is complete. That is precisely why the three upstream gap flags
remain explicit parts of the policy target rather than being assumed away.

Every upstream vocabulary is recognised by exact string value, without
importing the domain that owns it. An unrecognised context status, formula
status, category or applicability status is an invariant error, never a guess.

## Exact policy only — the same direction is not the same policy

A policy rule's target is exactly:

```text
(category,
 frozenset{(step8c_rule_id, step8c_rule_version), ...},
 signal_set,
 has_identity_unresolved,
 has_identity_ambiguous,
 has_personal_evidence_gap)
```

There is deliberately no rule of the form `SUPPORTING_ONLY → BUY`. Two
entirely different published claims can both carry `SUPPORTING` while
deserving opposite product actions, so a policy keyed on a direction would be
a sweeping unreviewed judgement wearing the costume of a lookup table.

The mandatory adversarial case: two aggregations both `SUPPORTING_ONLY`, one
carrying `{rule.a@1}` and one carrying `{rule.b@1}`, with a policy reviewed
only for the first. The first decides; the second returns
`NOT_ENOUGH_DECISION_POLICY`.

**A semantic version change invalidates the policy.** A rule reviewed for
`rule.a@1` does not match `rule.a@2`. When the underlying claim is revised,
the product decision built on it goes back for review.

**Exact set means exact set.** `{a@1}` matches neither `{a@1, b@1}` nor `{}`
nor `{a@2}` nor `{b@1}`, however similar the direction looks. Matching is
order-insensitive — a `frozenset`, so Step 8D's encounter order is irrelevant
to policy identity while remaining preserved on `source_aggregation`.

**Duplicate occurrences change nothing.** One semantic rule at positions 0, 3
and 8 is one distinct identity in Step 8D, so the policy target and the match
are identical to the single-occurrence case. Occurrence count is not policy
weight.

**The gap flags are exact too.** No wildcards: a policy targeting
`has_identity_unresolved = False` does not match a state where it is `True`.
Whether an unresolved ingredient or a missing evidence path should block an
action is itself a policy question, and infrastructure inventing a universal
`any gap → SKIP` — or a universal "a gap never matters" — would be the same
overreach as inventing a direction.

## No fallback, no wildcard, no priority

There is no closest match, no subset match, no superset match, no fuzzy
matching, no optional target field, no `None`-means-anything, no predicate
callback, and no rule priority, precedence, rank or specificity. No policy
means `NOT_ENOUGH_DECISION_POLICY`, full stop.

Two policies targeting the same governed state make the registry invalid. That
is an error, not a tie-break opportunity: not resolved by version recency, not
by declaration order, and not by preferring the "safer" action — preferring
`SKIP` is no more reviewed than preferring `BUY`. Duplicate targets are
rejected even when both name the same action. Retiring a policy version is
deliberate future governance, never something a lookup infers from a number.

## No score, no vote, no inference from words

No score, points, weight, magnitude, rating, grade, rank, net, majority,
ratio, percentage, average, threshold or confidence number exists anywhere in
the layer. Policy is exact lookup. A static test rejects that vocabulary in
executable code and rejects every ordering comparison (`<`, `<=`, `>`, `>=`)
in production, because a score or a tie-break needs one somewhere.

Ten supporting semantic rules and one cautionary rule give Step 8D `MIXED`.
Step 8E may act only if a policy was reviewed for that exact eleven-rule
identity set; a policy reviewed for a different one-plus-one `MIXED` set does
not match.

Step 8E never inspects claim prose, scope, evidence strength or tier, sources,
matched facts, ingredient names, or ambiguity candidates. Identifiers are
opaque: a semantic rule called `rule.caution.severe` is a string, and no
action is derived from how it reads.

Three static guards make an implicit direction-to-action mapping structurally
impossible rather than merely absent:

1. Production never *names* an action member — `PersonalDecisionAction.BUY`
   and its siblings appear nowhere, so a lookup table or an `if` chain cannot
   be written.
2. Production never *constructs* one by call, closing the string-built route.
3. Every non-`None` `action=` argument is sourced from a `.action` read on a
   matched rule.

## Pure, and adds nothing to the database

Step 8E performs 0 SQL queries, 0 writes, 0 HTTP calls, 0 AI calls, 0 Open
Food Facts reads, 0 filesystem policy loading, 0 caching and 0 telemetry side
effects. Every public function is synchronous. There is no API route and no
frontend surface.

It adds no migration, ORM model, table, column, index, evidence claim type,
persistence or production seed; the Alembic head is unchanged at `b8c9d0e1f2`.
All public dataclasses are `frozen=True, slots=True`, and all collections are
tuples or frozensets.

`source_aggregation` is the exact Step 8D instance that was passed in —
asserted with `is`, never copied or rebuilt — so the complete upstream chain,
including `source_semantics` and everything Step 8E declines to interpret,
remains reachable for Step 8F.

## Red lines

- **`WAIT` is a reviewed action, never a stand-in for uncertainty.**
- **The same direction is not the same policy.** Exact semantic identity sets.
- **A claim version change invalidates the policy match** until reviewed again.
- **Complete semantic mapping is not complete product knowledge.** The gap
  flags stay explicit.
- **No wildcard, no fallback, no priority.** A reviewed conflict is an error.
- **Production emits no action.** The registry is empty by design.
- **Step 8F owns the explanation.** No user-facing sentence lives here.
