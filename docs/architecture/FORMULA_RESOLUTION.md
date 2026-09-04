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

## 3. Only a top-level comma is a V1 delimiter — and not every one of those

Comma is the ordinary ingredient-list separator and the only character explicit
enough to act on. A top-level comma is a delimiter *unless* it sits inside a
chemical locant, which §6 describes.

Every other character falls into one of two buckets, and the distinction
matters more than it looks.

### Bucket A — preserved inside the entry

These genuinely occur **inside** ingredient and trade names, and sit visibly
*within* a line, so there is no rival reading in which they separate two printed
entries:

| Preserved | Because it appears inside |
| --- | --- |
| `/` | `Acrylates/C10-30 Alkyl Acrylate Crosspolymer`, `CI 77491/CI 77492`, `Aqua/Water/Eau` |
| `-` | `PEG-40 Hydrogenated Castor Oil`, `Sodium C14-16 Olefin Sulfonate`, `Vitamin-E` |
| `&`, `+`, "and" | all appear inside supplied trade names |
| space, tab | `Butyrospermum Parkii (Shea) Butter` — a name is usually several words |

Splitting on any of these would shatter one ingredient into fragments that name
nothing.

### Bucket B — boundary-ambiguous, so the formula is withheld

A **top-level line break** or **semicolon** may be a boundary between two
printed entries, or may be internal to one — a visual wrap in a long name, or a
semicolon a supplier put in their own trade name. Punctuation alone cannot tell.

These return `AMBIGUOUS_BOUNDARY`: **zero tokens, and Step 7A is never asked.**

> An earlier version of this document claimed such a list "parses as a single
> entry, which then does not resolve". That was wrong, in exactly the way §6
> describes: it is a statement about today's registry, not an invariant.
> Step 7A's normalizer collapses *every* line-boundary character to a single
> space, so a merged `"Water\nGlycerin"` token looks up `water glycerin` — and
> the day a reviewed identity is published under the name `Water Glycerin`, a
> two-line label would silently resolve to one substance that was never in the
> product. `"Water; Glycerin"` has the same shape. Merging is a guess, not
> caution.

Covered: `\n`, `\r`, `\r\n`, `\v`, `\f`, `\x1c`, `\x1d`, `\x1e`, `\x85`,
`\u2028`, `\u2029` — Python's complete `str.splitlines()` set, with a test
asserting the constant still matches it, plus `;`. Bucket A is deliberately
*not* generalised into this rule.

**Grouping still wins.** A semicolon or line break inside balanced brackets is
protected content, not a top-level boundary: `Parfum (A; B), Water` is two
entries, and `Parfum (A\nB), Water` likewise.

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
| `AMBIGUOUS_BOUNDARY` | a comma, line break or semicolon that punctuation cannot place; nothing is emitted and nothing is resolved |
| `TOO_LONG` | longer than 4000 characters |
| `TOO_MANY_ITEMS` | more than 128 entries |

Every non-`PARSED` status returns **zero** entries. There is no partial success,
and nothing is ever truncated to fit.

### Locant commas are part of the name

`1,3-Butanediol` prints a comma that no bracket protects. Treating it as a
delimiter is not a miscount, it is destruction: the token never reaches the
identity layer, so **no reviewed identity claim can rescue it** — the name it
would match no longer exists by the time resolution runs. Worse, the fragments
left behind (`1`, `3-Butanediol`) are not permanently inert; they would begin
resolving the moment anybody published a canonical name matching one, turning a
parsing defect into confident false-positive ingredients.

So the scanner recognises the *punctuation shape* of a locant run:

```
locant_run  := atom ( ',' atom )+ '-' name_char
atom        := ( digit+ | heteroatom ) prime*
digit       := 0-9
heteroatom  := N | O | S | P
prime       := ' | ′
name_char   := any alphanumeric
```

preceded by the start of the entry, whitespace, an opening bracket, a hyphen, or
the previous comma of the same run.

Each clause earns its place:

| Clause | What it prevents |
| --- | --- |
| trailing `-` + name character | `CI 77491,CI 77492` merging — after `CI` comes `I`, not a hyphen |
| leading boundary | `Vitamin B3,2,6-Di-t-Butyl-4-Methylphenol` merging — the `3` of `B3` follows a letter, so it is not a free-standing locant |
| no whitespace inside a run | `Aqua, 1, 2, Glycerin` merging — that is an ordinary list |
| run start must be accounted for | `Acid Red 1,N-Methylpyrrolidone` being silently merged *or* split — it becomes `AMBIGUOUS_BOUNDARY` instead |

The comma is kept **verbatim**: `raw_name` is identical to what was printed,
never stripped, rewritten or normalised here.

**This is lexical analysis, not identification.** It asks whether a comma sits
inside a locant-shaped run of punctuation — never whether the surrounding text
names a real substance. There is no dictionary, no database, no Step 7A lookup
and no network in this decision, and a test asserts as much.

That separation is the invariant, and it is worth stating flatly: **knowledge
may decide what a token denotes; knowledge must never decide retroactively where
the printed token boundaries were.** If Step 7A were consulted while parsing,
publishing or retiring one canonical name would silently re-tokenise every
formula, and the same printed list would mean different things on different
days. A test publishes identities and asserts tokenisation does not move.

### When punctuation cannot decide, nothing is emitted

`Acid Red 1,N-Methylpyrrolidone` reads equally well two ways: a colour index
followed by a solvent, or one name carrying an `N` locant. The shape is
`substantive text`, a space, a bare locant atom, the comma, then a locant-shaped
run. No punctuation analysis separates the readings; only a dictionary could,
and this parser must not have one.

So the parser returns **`AMBIGUOUS_BOUNDARY`**, emits zero tokens, and sends
nothing to Step 7A.

> **The invariant.** When punctuation alone cannot defensibly determine whether
> a locant-shaped comma is internal to a name or a delimiter between two, Step
> 7B returns an explicit parse ambiguity and sends no names to identity
> resolution. **The registry must never determine token boundaries indirectly,
> through whether a merged or split reading happens to resolve.**

Two earlier attempts got this wrong in opposite directions, and both were wrong
the same way — they made correctness depend on what the registry happened to
contain:

| Attempt | Failure it invited |
| --- | --- |
| Split the comma | Fragments `1` and `3-Butanediol`. Publish canonical names for those and the parser starts reporting ingredients that are not in the product. |
| Merge the comma | The concatenation `Acid Red 1,N-Methylpyrrolidone`. Publish a canonical name matching it and the parser reports one confident ingredient that is not in the product. |

Neither "fails safe". Each is safe *only while nobody has published the name
that would make it unsafe*, which is not an invariant at all — it is a race
against the catalogue growing. Withholding both readings is.

An adversarial test publishes all three readings — `Acid Red 1`,
`N-Methylpyrrolidone`, and the concatenation — as fully valid canonical
identities, then asserts the formula still returns `AMBIGUOUS_BOUNDARY` with no
ingredients, that none of the three appears as winner or candidate, and that
`resolve_names` is never called at all. Its counterpart does the same for the
fragment direction.

**`AMBIGUOUS_BOUNDARY` is not `ResolutionStatus.AMBIGUOUS`.** They answer
different questions and are deliberately separate states:

| | Question | Answered by |
| --- | --- | --- |
| `ParseStatus.AMBIGUOUS_BOUNDARY` | *Where are the printed boundaries?* | this layer, lexically |
| `ResolutionStatus.AMBIGUOUS` | *Which entity does this established token denote?* | Step 7A, from evidence |

Collapsing them would let a caller treat an unreadable label as a readable one
with an undecided ingredient.

### What is unambiguous

A locant run is accepted when its start is accounted for:

- it **begins the current entry**, leading whitespace aside — `Water, 1,3-Butanediol, Glycerin`, where the entry is only `" 1"` when the comma arrives, so there is no competing reading;
- it is **bound by a hyphen** — `Benzene-1,2,4-Tricarboxylic Acid`;
- it **continues a run already accepted** — the second comma of `1,1,1-`.

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

`position` is **parsed printed-entry order, and nothing else**. One printed
ingredient is one entry, so the numbering matches what the pack shows.

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
