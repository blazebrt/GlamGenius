# Canonical substance identity

## What this layer answers

One question, and deliberately only one:

> **What exact substance or material does this exact reviewed name refer to?**

That is the whole of Step 7A. It is the foundation the cosmetic, skincare,
haircare and supplement layers will later stand on, and it is useless as a
foundation unless it is narrow.

## What it does not answer

Identity is **not** any of these, and none of them is a column on `substances`:

| Not this | Because |
| --- | --- |
| **Safety** | "Safe" is a claim about a person, a dose and an exposure route. A molecule is not safe or unsafe on its own. |
| **Function** | What a substance *does* depends on the formulation it is in, at what level, on what tissue. |
| **Efficacy** | Presence is not effect. "Contains niacinamide" establishes nothing about whether a product works. |
| **Concentration** | Identity says which thing; it never says how much. |
| **Regulatory status** | Permitted-ness is jurisdiction + category + date + source. It changes without the substance changing. |
| **Interaction** | A relationship between two substances in a context, not a property of either one. |
| **Appropriateness for a person** | There is no person here. This layer has no account, no device, no profile. |

Each of those is a claim about a substance **in a context**. Each needs its own
evidence, with its own applicability, reviewed on its own terms. Putting any of
them on the identity row would make it a global, unsourced assertion — exactly
what `PRODUCT_CONSTITUTION.md` forbids.

## Exact synonyms are not families, forms or related materials

This is the distinction the whole design protects, and the easiest one to lose.

A canonical identity system must never silently turn *related*, *form-of*,
*member-of-family*, *contains*, or *marketing shorthand* into **same substance**.

The legacy Care ontology groups things for routine matching, which is a
different and legitimate job. Its groupings are **not** exact identities:

| Legacy Care grouping | Why it is not an identity |
| --- | --- |
| `tocopheryl acetate` under a vitamin E concept | An ester of tocopherol is not tocopherol. |
| `ceramide np` / `ap` / `eop` under `ceramides` | Three different molecules under one family label. |
| `hydrolyzed keratin` / `silk` / `wheat protein` under one protein concept | Different source proteins entirely. |
| `peppermint oil` under `menthol` | A botanical material that *contains* a molecule is not that molecule. |

So there is **no automatic legacy alias backfill**. A legacy alias does not
become a canonical name merely because the old parser recognises it. If two of
those genuinely are the same entity, a separately reviewed identity claim says
so, deliberately, with a source.

## Ambiguity fails closed

The same printed text can genuinely denote two different things. When it does,
the resolver returns `AMBIGUOUS` and **refuses to pick**.

It does not break the tie by popularity, source count, evidence strength, the
old Care family, product category, ingredient position, alphabetical order, row
order, a heuristic, or a model. Every one of those would be the system inventing
an answer no reviewer gave.

For the same reason `substance_names.normalized_name` is **not** unique in the
database. A unique index would force a winner silently, at write time, by
insertion order, with nobody reviewing the choice.

## AI is not an identity authority

No model is consulted to resolve a name. Normalisation is a pure function;
eligibility is a set of boolean checks over reviewed rows. There is no fuzzy
matching, no edit distance and no embedding anywhere in this path.

This follows the constitutional architecture directly: *AI reads. Structured
intelligence knows. Deterministic rules decide. AI explains.* Identity is in the
"knows" and "decides" half, so AI has no part in it.

## Evidence remains the provenance authority

`EvidenceClaim`, `EvidenceSource` and `EvidenceClaimSource` are the **only**
claim/source authority in the product. Step 7A adds no second one.

There is no `substance_sources` table, no source URL on a substance row, no
second draft/review/publish state machine, no second reviewer state. The
substances domain contributes a narrow *adapter* that creates a correctly typed
draft, and everything after that is the ordinary evidence workflow:

```
create_identity_draft(...)                    -> draft
evidence.authoring.record_publication_verification(...)
evidence.authoring.approve(...)               -> approved
evidence.authoring.publish(...)               -> published
```

### `SubstanceName` is an index, not knowledge

Every name row exists because a claim said so, and points back at that claim.
It carries no review state of its own — no `review_status`, no `verified`, no
`approved` — because the claim already owns that, and a second copy would drift.

A name row may exist while its claim is still a draft. It is completely **inert**
until the claim clears the full public boundary, because eligibility is
re-derived from the claim on every read rather than cached on the row.

### The public boundary is stricter than approval

`assert_claim_approvable` guards rule provenance and accepts an
approved-but-unpublished claim. Identity resolution needs more, so
`claim_is_public_knowledge_path` is a **separate, stricter** predicate rather
than a tightening of the existing one — raising the existing bar would
retroactively invalidate seeded release evidence and silently change behaviour
for every current call site.

Resolution requires all of:

- `review_status == published`, with `reviewed_by/at` **and** `published_by/at`
- `claim_status == supported`, a graded `evidence_strength`, a non-empty rationale
- every publication-verification checkpoint true, and `unresolved_doubt` false
- `domain == substance`, `subject_type == substance`, `subject_key ==` the
  substance's key, `claim_type == substance_identity`,
  `evidence_tier == reference_data`
- at least one **supporting** source link, reviewed, on an **active** source with
  an openable `http(s)` URL, an explicit licence/use note, and an allowed type

Allowed identity source types are deliberately narrow: `official_regulation`,
`government_reference`, `ingredient_reference_database`,
`manufacturer_technical_document`. `other` is excluded because it is what the
authoring tool assigns when nobody has classified a source, and marketing
material is excluded because a trade name standing in for a molecule is normal
there.

### Drift fails closed

A name row is never trusted just because it has a `identity_claim_id`. At
resolution time the claim's `structured_value` is re-parsed and the exact row
must still appear in it, with matching namespace and preferred flag, and the
claim's `entity_kind` must match the substance's. A hand-edited database or a
malformed historical row is rejected, not resolved.

## Normalisation removes typography, never chemistry

`normalize_name` does exactly four things: NFKC, trim, collapse internal
whitespace, casefold.

It deliberately does **not** strip punctuation (`1,3-butanediol` vs
`1,4-butanediol`), singularise, stem, transliterate, drop hydrate/salt/form
information (`retinal` is not `retinaldehyde`), guess abbreviations, or rewrite
separators (`vitamin-e` does not become `vitamin e`).

Two different molecules that normalise to one key are indistinguishable
downstream, and the resolver would then answer `RESOLVED` with the wrong
substance. Silence is recoverable; a wrong identity is not.

`normalized_name` is always computed by the server. A caller-supplied key would
let a writer choose what its own row matches.

## One name at a time — no ingredient list parsing yet

Step 7A resolves **one candidate token**. It does not tokenise
`"Water, Niacinamide, Glycerin"`, and will not pull `Niacinamide` out of it.
There is no substring search and no longest-alias matching.

Splitting a printed list into candidate names is **Step 7B**, and doing it
implicitly here would mean guessing where one name ends and the next begins.

## No concentration inference, ever

Ingredient list position is **not** a concentration. This layer knows nothing
about how much of anything is present: no approximate percentage, no inferred
band, no "high in formula" or "low in formula".

If a percentage is explicitly printed on a label, that is an observed formula
fact for a later milestone, with its own provenance.

## Store A stays separate

`substances` and `substance_names` are proprietary Store B. Nothing is written
into Open Food Facts' Store A, no substance key is stored on an OFF record, and
OFF is never a source of canonical identity. A future runtime path may join a
label string to proprietary knowledge at query time without mutating Store A —
Step 7A does not perform that integration. See `ODBL_DATA_WALL.md`.

## No bulk import happened

No ingredient database was imported in Step 7A. Nothing was scraped from CosIng,
PubChem, CIR, manufacturer sites or Open Food Facts. **Zero production canonical
names ship in this milestone.**

That is the intended outcome, not a shortfall. Every identity must carry its own
provenance, URL, publisher, source type and licence/use note. Shipping an
unsourced catalogue to look complete would be the exact failure this
architecture exists to prevent: *"not enough information" is preferable to
guessed identity.*

## What is deliberately still ahead

- **Step 7B** — cosmetic formula tokenisation and category interpretation. That
  layer will split ingredient lists and call this one; this one will not grow
  a parser.
- **Supplements migration** — `SupplementLabelComponent` and
  `SupplementComponentKnowledge` keep their current behaviour untouched. Moving
  them onto canonical identity is its own reviewed change.
- **Legacy Care migration** — `routines/ontology.py` and its parser keep working
  exactly as before. They are legacy Care behaviour vocabulary awaiting an
  explicit, separately reviewed migration.
- **Contextual claims** — function, safety, regulatory status and interaction,
  each with its own evidence and applicability.
