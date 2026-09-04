# Formula resolution

## What this layer answers

One question, and deliberately only one:

> **What exact candidate names were printed, and which canonical identities can
> we defensibly resolve them to?**

That is the whole of Step 7B. It is a deterministic bridge:

```
printed ingredients_text
  → conservative top-level parsing
  → ordered exact candidate names
  → one Step 7A batch identity resolution
  → ordered formula identity result
```

## 1. Parsing is separate from identity

The parser finds the boundaries between printed entries. It does not know what
any entry means and never asks. Identity is decided entirely by Step 7A, whose
rules — published evidence, exact names, ambiguity preserved — are documented in
`SUBSTANCE_IDENTITY.md` and are not restated, weakened or worked around here.

There is no second normalizer, no second synonym table, no second alias map and
no matching rules of this layer's own. Step 7A's `normalize_name` is the only
canonical normalisation in the product.

## 2. Parsing is separate from interpretation

This layer produces **no** score, grade, verdict, action, recommendation,
positive or negative. It does not decide what a substance does, whether it is
safe, whether it is permitted, how much is present, or whether the product suits
anybody.

Identity presence is not efficacy. A formula containing niacinamide is a formula
containing niacinamide; whether the product *does* anything is a different
question, needs its own evidence with its own applicability, and belongs to a
later milestone.

## 3. Only a top-level comma is a V1 delimiter

Comma is the ordinary ingredient-list separator and the only character explicit
enough to act on. Every other candidate was considered and rejected, because
each occurs *inside* real INCI names:

| Rejected | Because it appears inside |
| --- | --- |
| `/` | `Acrylates/C10-30 Alkyl Acrylate Crosspolymer`, `CI 77491/CI 77492`, `Aqua/Water/Eau` |
| `-` | `PEG-40 Hydrogenated Castor Oil`, `Sodium C14-16 Olefin Sulfonate`, `Vitamin-E` |
| `;` | rare as a separator; a list using it deserves a human look, not a guess |
| newline | a wrapped line inside one long name is indistinguishable from a break between two |
| `&`, `+`, "and" | all appear inside supplied trade names |

A list that genuinely uses one of those as its separator parses as a **single
entry**, which then does not resolve. That is the intended failure: one
unresolved entry is recoverable, and a name invented by splitting is not.

## 4. Chemical punctuation is preserved

Slashes, hyphens, digits, percentages, casing and any bracketed text survive
into `raw_name` exactly as printed. Only surrounding whitespace is removed.
Nothing is stemmed, singularised, transliterated, de-accented or rewritten.

## 5. Grouping protects internal commas

Balanced `(...)`, `[...]` and `{...}` are tracked, including nesting, and a comma
inside them is not a delimiter.

`Parfum (Fragrance, Aroma), Limonene` is **two** entries.

The text inside a grouping is preserved and **never interpreted**.
`Water (Aqua/Eau)` is one entry, not three: which identities a parenthetical
names is a question for a reviewed identity claim, not for a parser guessing at
what an abbreviation meant.

## 6. Malformed lists fail closed — the whole list

Unbalanced grouping, a mismatched closer, or an entry with no text in it makes
the **entire** formula `MALFORMED`. Not the entry that went wrong, and not the
tail after it.

Returning the well-formed prefix would hand a caller an ingredient analysis that
looks complete and silently omits everything after the first unclosed bracket.

Empty entries are malformed rather than dropped, for a related reason:
`Water,,Glycerin` is not "water and glycerin", and silently deleting the empty
entry would renumber every position after it.

| Status | Meaning |
| --- | --- |
| `PARSED` | read whole; entries returned in printed order |
| `EMPTY` | absent, empty, or whitespace only |
| `MALFORMED` | unbalanced grouping, or an entry with no text |
| `TOO_LONG` | longer than 4000 characters |
| `TOO_MANY_ITEMS` | more than 128 entries |

Every non-`PARSED` status returns **zero** entries. There is no partial success,
and nothing is ever truncated to fit.

### Known limitation: a locant comma splits

`1,3-Butanediol` prints a comma that is not inside any bracket, so the V1 rule
cannot tell it from the comma between two ingredients. It becomes `1` and
`3-Butanediol`.

This is accepted for V1, and three things make it the safe failure:

- Neither fragment resolves, so **no wrong identity is ever produced** — the
  outcome is `UNRESOLVED`, which is this layer's honest answer.
- The alternative is worse. Recognising `digit,digit-` as "inside a name" is
  exactly the pattern-guessing the parser refuses everywhere else, and it would
  mis-split the day a list legitimately prints `Titanium Dioxide, 1,3-Butanediol`.
- The real fix is a reviewed identity claim recording the exact printed form,
  not a cleverer parser.

What it costs: the entry count, and every `position` after it, shift. A consumer
must therefore never treat `position` as authoritative pack order — which it
already must not, for the separate reason in §10.

## 7. No substring, prefix or fuzzy matching

A canonical `Niacinamide` does **not** make any of these resolve:

```
Niacinamide 5%      contains niacinamide      niacinamide complex
5% Niacinamide      Niacinamide Solution      Niacinamid
```

Only an exact reviewed name resolves. Explicit percentage parsing is not Step 7B
at all; a printed percentage is an observed formula fact for a later milestone,
with its own provenance.

## 8. No legacy Care alias fallback

When canonical resolution comes back unresolved, that is the answer. The layer
does not consult `routines.ontology`, `routines.parser`, `IngredientAlias` or the
legacy Care families, and a structural test asserts it cannot import them.

The Care ontology groups things for routine matching — a different and
legitimate job — but its groupings are not exact identities. Seeing
`tocopheryl acetate`, `ceramide ap`, `hydrolyzed keratin` or `peppermint oil` in
a formula does not make the old family map canonical.

## 9. No AI, no network

Parsing is a pure function. Resolution is a set of boolean checks over reviewed
rows. Nothing here calls the AI gateway, and nothing makes an HTTP request:
no PubChem, no CosIng, no manufacturer site, no Open Food Facts.

There is deliberately no "unresolved → ask a model" fallback. `UNRESOLVED` is a
legitimate answer, and inventing one would defeat the whole layer.

## 10. Formula order is not concentration

`position` is **printed label order and nothing else**.

It is never read as concentration, percentage, importance, dominance, efficacy
or risk. Ordering conventions do exist in some regulatory regimes, but applying
one requires jurisdiction, category and date — that is an evidence-backed
regulatory interpretation, not a fact about a list. None of "first ingredient is
the most abundant", "the top five matter", or "everything after fragrance is
below 1%" is inferred here.

## 11. Presence is not efficacy

No identity is translated into a function or a benefit. Not
`Niacinamide → brightening`, not `Glycerin → humectant`, not
`Salicylic Acid → exfoliant`, not `Ceramide → barrier repair`.

## 12. Ambiguity is preserved

If Step 7A says `AMBIGUOUS`, this layer says `AMBIGUOUS`, reports every candidate
key, and populates no winner. The tie is not broken by printed order, product
category, namespace, preferred-name flag, source count, evidence strength, the
old Care families, alphabetical order or row order.

There is no score, confidence, probability, similarity or rank anywhere in the
result — there is nothing to be uncertain about, only something that has or has
not been reviewed.

## 13. Groups and mixtures are not expanded

Step 7A can identify a `mixture` or a `group`. When a reviewed name resolves to
one, that identity is what is reported. It is never exploded into guessed
members: **a reviewed group name is not permission to decide which exact member
was in the formula.**

## 14. No persistence in Step 7B

No tables, no columns, no migration. A formula result is computed from an input
string plus canonical Store-B knowledge and discarded with the response. There
is no `formulas` table, nothing is written to `LabelSnapshot`, and nothing goes
anywhere near Open Food Facts' Store A.

The engine is also wired to nothing: no API route exposes it, and a structural
test asserts no module outside the domain imports it. Integration comes only
after the semantics are proven.

## 15. What is deliberately still ahead

- **Step 7C** — evidence-backed category interpretation. It will read these
  identities; it will not modify identity semantics, and it will not loosen
  anything above.
- **Observed formula facts** — printed percentages and other on-pack claims,
  each with its own provenance.
- **Product Result integration** — only once this layer's semantics are settled.
- **Contextual claims** — function, safety, regulatory status and interaction,
  each with its own evidence and applicability.
