# First production knowledge pack

Step 8I is the first real scientific judgement admitted to the governed FOR YOU
chain. It is deliberately one narrow pack: `petrolatum`, in `skin_care`, when the
trusted user-declared fact `care_skin_usual_feel` is exactly
`often_dry_or_tight`.

## Why this knowledge

Petrolatum was selected because its identity can be anchored to the governmental
PubChem/ChemIDplus record and two independent evidence paths support the narrow
ingredient-level applicability. Current American Academy of Dermatology Association
guidance names petrolatum among ingredients to look for in a cream or ointment for
dry skin. A randomized, double-blind, four-arm crossover study in healthy volunteers
with dry skin found that the petrolatum component improved barrier function through
reduced transepidermal water loss. The study explicitly was not designed to evaluate
therapeutic benefit.

Evidence strength is `moderate`, not `strong`: the evidence is about an ingredient or
component, not the concentration or suitability of every commercial formula, and not
a therapeutic trial.

## The exact reviewed decision

The semantic direction `supporting` and policy action `buy` are two separately
authored reviewed facts. There is no `supporting => buy` algorithm. The policy matches
only the exact Step 8I semantic identity, `supporting_only`, with all three structural
gap flags false. Step 8E's existing prerequisites also require available context, a
parsed formula, and complete semantic mapping.

Consequently, `Petrolatum, Glycerin` does not inherit the action. An unresolved or
ambiguous additional ingredient, an ingredient with no matching personal evidence,
or an applicable claim with no semantic mapping leaves a structural gap and the
existing chain withholds the decision. The pack adds no formula-length rule,
concentration inference, or product-level claim.

The AAD path is the displayed citation because it directly anchors the reviewed
product-selection context. PubMed supports the evidence review and strength rationale;
it is not selected as the one customer citation. Only source metadata and short
locators are stored. No AAD article prose or PubMed abstract is reproduced.

## Version and activation boundary

The pack is version-controlled so its constants and compiler can be independently
reviewed. It is not imported by normal runtime or bootstrap code. Deployment alone
does nothing: the evidence must pass the existing Step 7A and Step 8G governance
workflows, the exact serialized Step 8G entry must compile through the pack, and the
manifest must pass the existing Step 8H review and activation workflow. A new evidence
version cannot be inherited by the old release; it needs a new reviewed pack/release.

Production activation is therefore an explicit post-merge operation, never a
migration, startup hook, or reference-data seed.

## Evidence basis vs. customer-selected reason

These are two different scopes, and conflating them is the mistake this section
exists to prevent.

**Overall evidence basis (Step 8G review).** The reviewed claim rests on two
independently checked sources: the AAD dry-skin guidance page, which includes
petrolatum among cream/ointment ingredients to look for, and the randomized
PubMed study (PMID 31532576), in which the petrolatum component improved barrier
function with reduced transepidermal water loss. Evidence strength is `moderate`
because the evidence is ingredient/component-level rather than an exact-product
therapeutic trial, and because the study was not designed to evaluate therapeutic
benefit. The summary and strength rationale may — and do — describe both paths.

**Customer-selected reason and citation (Step 8F).** Step 8F shows one reason
beside one citation, so the reason may only assert what that one citation
supports. The selected citation is the AAD page at the exact locator
*"What skin care products are best for dry skin? / Ointment or cream"*. At that
locator the page supports a narrow proposition: dermatologist guidance lists
petrolatum among ingredients to look for in a cream or ointment for dry skin. It
does **not** establish the moisture-loss mechanism. That mechanism comes from the
PubMed study, which is part of the evidence body but is *not* the citation the
customer sees.

Attaching the mechanism to the AAD citation would cite a source for a claim it
does not make. So the reason is scoped to its own source:

```text
reason key: for_you.skin_care.petrolatum.dry_skin.dermatologist_guidance
```

Narrowing the customer reason does not narrow the evidence review behind it, and
the strength stays `moderate`. Tests hold both halves of that boundary.

## Future reason intent

Reviewed intent, not yet wired to any customer API or copy catalogue. An original
GlamGenius paraphrase, never reproduced source wording:

> For dry skin, dermatologist guidance includes petrolatum among ingredients to
> look for in a cream or ointment.

The reason attached to the AAD citation must not say *reduces moisture loss*,
*prevents water loss*, *repairs the barrier*, *heals* or *treats* dry skin, or
that the ingredient is *safe* or *recommended for everyone*. Some of those are
different claims; some belong to the other source; none is carried by this
citation.

## Provenance is absent, not inferred

Optional source metadata is left null where the source does not establish it. A
null is a statement that the source is silent, not an oversight:

| Field | Value | Why |
| --- | --- | --- |
| AAD `publication_date` | `null` | The page reports "Last updated: 1/2/26" — an update is not a publication date |
| AAD `version_or_revision` | `Last updated 2026-01-02` | Where the update date is recorded honestly |
| AAD `jurisdiction` | `null` | The page states no territory |
| PubMed `publisher` | `Wiley Periodicals, Inc.` | The article carries "© 2019 Wiley Periodicals, Inc."; PubMed is the citation location, not the publisher |
| PubMed `jurisdiction` | `null` | The record states none |
| Identity `publisher` | `ChemIDplus` | PubChem hosts the record; ChemIDplus is the depositor it names |

The compiler rejects each of these fields being filled with an inferred value —
including `2026-01-02` as a publication date, `global`/`US`/`international` as a
jurisdiction, and `PubMed` as the article publisher.
