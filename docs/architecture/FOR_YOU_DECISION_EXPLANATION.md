# FOR YOU decision explanation and source contract

## Step 8F boundary

Step 8F answers one narrow question:

> Given one exact Step 8E result, may that decision actually be presented to
> the customer, what one reviewed reason accompanies it, and which exact
> already-eligible openable source supports that reason?

The rule that governs everything else here:

```text
NO REVIEWED EXPLANATION + SOURCE  =  NO PRESENTABLE BUY / WAIT / SKIP
```

Step 8F is deliberately **stricter than Step 8E**. A reviewed action existing
inside the policy layer is not permission to show it. GlamGenius says why, and
says why with a source; a verdict that cannot name its reviewed reason and a
named openable source is an unsourced claim, so it is withheld entirely —
`NOT_ENOUGH_EXPLANATION` with `action=None`, not the action "because we know
it really is a BUY". That reasoning is exactly how an unsourced verdict
reaches a screen.

The governed chain now ends:

```text
… → Step 8D → Step 8E — exact reviewed BUY / WAIT / SKIP policy
             → Step 8F — one reviewed reason + exact openable source
             → future API / frontend integration
```

## The source-continuity correction

Step 8B holds the reviewed claims and, on each, the real
`PersonalApplicabilitySource` objects — `source_key`, `title`, `publisher`,
`canonical_url`, `locator`, `publication_date`, `version_or_revision`,
`jurisdiction`.

Step 8C deliberately reduces a claim to its identity and reviewed direction.
That is right for deciding and wrong for presenting: it left the downstream
object graph with no route back to the sources a customer must be shown.

The fix is one additive field at the end of Step 8C's result:

```python
source_personal_applicability: LabelSnapshotPersonalApplicability | None = None
```

set on **every** production return path — ordinary context, handoff, and
missing context — to the exact same instance, asserted with `is`. Step 8C
already depended on `personal_applicability`, so this adds no dependency, and
it changes no semantic decision. Step 8D and Step 8E needed no change at all:
both already carry their exact upstream object forward. The chain is now

```text
policy → source_aggregation → source_semantics → source_personal_applicability
```

and reaches the real Step 8B source objects.

### Why copying URLs into the lower layers is forbidden

The tempting shortcut is to put `canonical_url` on a semantic rule, or the
source list on a Step 8C projection. Every version of that creates a **second
evidence record** — one reviewed, one copied — free to drift apart. The
citation a customer sees would then come from the copy, and could quietly stop
matching the source that was actually reviewed. Carrying the object cannot
drift; a copy always can.

Equally forbidden: attaching sources to semantic rules (they describe
direction, not provenance) and building any second evidence store.

## Exact explanation, exactly targeted

An explanation rule is keyed by `(policy_id, policy_version, action)` and
carries the full evidence anchor: semantic rule id and version, substance key,
claim key and version, source key and locator, plus a `reason_key`.

There is no `BUY → positive explanation` rule, and no fallback of any kind —
not by action, signal, category, "closest" match, or an older policy version.
An explanation written for one reviewed decision cannot be reused on another
that happens to reach the same action, because the reason is specific to the
evidence the reviewer read.

The anchor is verified against the live governed state before anything is
presented. All five parts must match exactly one distinct Step 8D aggregated
rule. Matching on the semantic rule id alone, or the claim key without its
version, would let a reason written against one finding travel to a revised
one.

Two explanations for a single reviewed decision make the registry invalid —
even when they name the same source and the same reason. Choosing between them
by version recency, declaration order, shortest reason or apparent source
quality would be an unreviewed editorial decision about what a customer is
told, made by a lookup.

## Why one source is explicitly selected

A claim may carry several eligible sources. Step 8F does not pick one: a
reviewer already did, by identity, and Step 8F matches `source_key` **and**
`locator` exactly — no normalising, no case folding, no punctuation stripping.

If the named source is absent, the decision is not presentable. It does not
fall back to another source on the same claim. Substituting one would attach a
reviewed sentence to evidence nobody reviewed it against.

**Source metadata cannot select itself.** Title, publisher and publication
date are copied into the citation only *after* selection, and a static test
confines every read of them to the citation builder. Otherwise the citation
shown beside a decision could change because a publisher renamed a document —
which is not a citation.

## WAIT is still not uncertainty

Unchanged from Step 8E and re-proved here. `WAIT` is a reviewed product
action. Every negative state in Step 8F is an absence of one:

| Status | Meaning |
| --- | --- |
| `HANDOFF_REQUIRED` | a professional should be involved |
| `NOT_ENOUGH_INFORMATION` | the system was not entitled to decide |
| `NOT_ENOUGH_DECISION_POLICY` | no reviewed decision policy exists |
| `NOT_ENOUGH_EXPLANATION` | a reviewed action exists but cannot be shown |

Each blocked path is tested with a synthetic `WAIT` explanation injected that
would match if the registry were ever consulted, and each must still refuse.

Handoff and every Step 8E non-decision are answered **before** the explanation
registry is built, so a broken registry can neither suppress a handoff nor
turn a structural non-decision into an exception. The handoff `reason` and
`message` pass through unchanged from the canonical hard-handoff authority:
rewriting or embellishing them would put a second, unreviewed voice on the
most sensitive sentence the product says.

## Copy keys, not sentences

Step 8F emits keys — `for_you.verdict.buy`, `for_you.not_enough.explanation`,
and the reviewed `reason_key` from the rule. No English is written in
`service.py`, and a static test rejects a literal that looks like a sentence.

User-facing strings live in the frontend catalogue where a reviewer reads them
all at once against `LEGAL_RULES.md`. Composing prose from evidence in a
service would put the most legally sensitive text in the least reviewable
place — and would be generation, not review.

Resolving those keys into copy belongs to the later API/frontend milestone.
Step 8F adds no route, no screen and no string catalogue entry.

The verdict key is a rendering choice, not a decision: it labels the action
Step 8E already supplied. The mapping is keyed by the action's string value
rather than the enum member, specifically so that no production line anywhere
names BUY, WAIT or SKIP — which lets the guard against inventing an action
stay absolute, with no carve-out for "but this one is only a label".

## The legal requirement this closes

Every negative statement eventually displayed to a customer must have its
source available to the presentation layer. `DECISION_PRESENTABLE` is the only
status carrying an action, and it cannot be constructed without a citation:
the invariant is enforced in `__post_init__`, so a decision without its source
is not merely unreached but unrepresentable.

## Pure, and adds nothing to the database

0 SQL, 0 writes, 0 network, 0 AI, 0 Open Food Facts, 0 filesystem loading, 0
cache, 0 telemetry. Every public function is synchronous. No migration, ORM
model, table, column, index, evidence claim type, persistence, seed, API route
or frontend surface; the Alembic head is unchanged at `b8c9d0e1f2`.

`personal_decision_policy` is the only application-domain dependency, asserted
as an allowlist. Everything below Step 8E is traversed through the preserved
objects and treated structurally — never re-queried.

`result.source_policy is policy` holds exactly, so the whole evidence chain
stays inspectable internally even when nothing may be shown.

## Red lines

- **No reviewed explanation and source, no presentable action.**
- **`WAIT` is a reviewed action, never uncertainty.**
- **Exact explanation targets.** No wildcard, no fallback, no closest match.
- **A reviewer names the source.** Metadata never selects it.
- **No prose generation.** Copy keys only; no AI; no paraphrase.
- **No action inference.** The only action emitted is the one Step 8E decided.
- **No score, rank or tie-break.** Exact lookup.
- **Production emits nothing.** All three governed registries — semantic,
  policy and explanation — are empty by design.
