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

## Future reason intent

Reviewed intent, not yet wired to any customer API or copy catalogue:

> Petrolatum can help reduce moisture loss when your skin feels dry or tight.
