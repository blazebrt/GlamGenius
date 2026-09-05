# Category-specific substance interpretation

## The boundary

Step 7C answers one narrow question:

> For an explicitly selected label snapshot, an already-resolved printed
> formula, and an explicit caller-supplied product category, which reviewed and
> published reference-role claims exist for each exact canonical substance?

The layers remain deliberately separate:

```text
Step 7A: canonical identity
      ↑ consumed only by Step 7B formulas

Step 7B: printed formula → identity projection

Step 7B.1: binds that projection to one explicit LabelSnapshot

Step 7C: a sibling read-only domain consumes the projection and attaches
         eligible category-specific public evidence
```

Step 7C intentionally does not import the canonical identity domain. Identity
is an upstream answer, not a service Step 7C may reopen. The local
`ProjectedIdentityStatus` enum is only a strict view of the value already in the
projection; an unknown upstream status fails rather than being guessed.

Step 7C intentionally does not import the formula domain. The Product
formula-projection adapter is the established external boundary. It preserves
the exact snapshot ID, barcode, version, content fingerprint and scan event.
The convenience function projects the supplied snapshot and never selects a
latest one.

## Explicit category and exact identity

The caller supplies exactly one of `packaged_food`, `skin_care`, `hair_care`,
or `cosmetics`. Step 7C never derives category from a name, barcode, formula,
Open Food Facts, AI, or the old Care taxonomy. The mappings are, respectively,
`nutrition`, `skin_care`, `hair_care`, and `cosmetics`; cosmetics is not
skincare.

Only an upstream `resolved` row with a non-null canonical `substance_key` may
reach evidence. `unresolved` and `ambiguous` are terminal honest results.
Ambiguity candidates are preserved but never queried, and category evidence is
never used to choose between them. Groups and mixtures stay the exact group or
mixture established upstream; they are not expanded.

## Public evidence gate

An exposed claim must match the exact category domain, `subject_type` of
`substance`, canonical subject key, and
`substance_category_interpretation` claim type. Its nested schema must be V1
`reference_role`, its tier must be `reference_data`, its strength must be
strong, moderate, or limited, and it must pass the shared public-claim gate.

At least one supporting path must pass the shared public-source gate. V1 admits
only official regulations, official guidelines, government references,
ingredient reference databases, and manufacturer technical documents. Every
returned claim carries the original reviewed summary, scope, strength, tier,
and complete openable source provenance. Step 7C synthesises no claim text.

## Bounded, live, and read-only

For a valid formula, Step 7C deduplicates only lookup keys, performs at most one
candidate-claim query and one source-path query, validates every bounded
candidate in memory, and restores claims to every printed occurrence in
printed order. No resolved identities means zero queries; no candidate claims
means the source query is skipped. An invalid candidate can only cost itself.

LabelSnapshot is immutable historical observation, while the identity and
evidence registries are live reviewed knowledge. The same snapshot may expose a
newly published claim later or stop exposing a retired one without changing the
snapshot, its fingerprint, scan history, or formula projection. The result is
never persisted.

There is no score, grade, verdict, efficacy, safety, risk, benefit,
concentration, recommendation, personal context, network lookup, Open Food
Facts access, or AI in this layer.
