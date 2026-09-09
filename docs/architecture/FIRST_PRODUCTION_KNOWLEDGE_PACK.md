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

## Knowing what packs exist, once there is more than one

One pack fits in a person's head. A dozen will not, and by then the questions
that matter are boring and structural rather than scientific: which packs exist,
does each still own a unique `PACK_ID`, does each own a distinct `REASON_KEY`,
does each still declare the compiler the Step 8H release workflow calls by name.

`backend/app/knowledge_packs/inspection.py` answers exactly those questions, and
an offline command reports them:

```bash
python scripts/inspect_knowledge_packs.py
python scripts/inspect_knowledge_packs.py --json
```

Exit `0` means every committed pack satisfies the structural contract; a non-zero
exit names the file, the field and the finding.

**It parses source. It never runs it.** Candidate pack files are read as text and
parsed with `ast`. No pack is imported, executed, `eval`-ed, `exec`-ed or run
through `runpy`. That is not tidiness — part of the point of an inventory is to
notice a pack that has grown an import-time side effect (a database call, a
network call, a file write, a provider call, a release operation), and a tool
that imported packs to inspect them would trigger every such side effect in the
repository at once, on an operator's laptop, in the course of asking a question.
Tests hold the property directly: a synthetic pack that writes a sentinel file at
module level is inspected, and the sentinel does not appear.

Be precise about what that buys. Three sentences, and no more than three:

- The inspector does not execute candidate source.
- Every statically represented binding attempt that occurs in the module's own
  execution scope is observed, for the five governed names.
- Nested lexical and class-body locals are not treated as module declarations.

It is **not** a claim that any pack has been proven side-effect-free, and no
attempt is made to see through dynamic tricks such as `exec(...)` or
`globals()["PACK_ID"] = …`. Step 14A does not sandbox Python.

The consequence is that identity must be **statically legible**, and the check
for that runs in two layers.

**Layer one sees every attempt.** The inspector walks module scope and records
every statement that binds, rebinds or deletes one of the five governed names —
`PACK_ID`, `DOMAIN`, `CATEGORY`, `REASON_KEY`, and
`build_release_manifest_from_published_entry`. Plain and annotated assignment,
destructuring and starred targets, chained and augmented assignment, walrus
expressions, imports, `del`, loop targets, `with ... as`, `except ... as`, `match`
capture patterns, and function and class definitions all count, including inside
module-scope control flow (`if`, `for`, `while`, `try`, `with`, `match`), because
those bodies really do bind module names when they run.

It also counts **definition time**, which is easy to forget. A `def` is a
statement before it is a scope: its decorators, default values, annotations and
return annotation are evaluated *where the `def` sits*, in the enclosing scope, at
the moment the statement executes. So are a lambda's defaults, and a class's
decorators, bases and keywords. A walrus in any of them binds a module name:

```python
def helper(value=(PACK_ID := "for_you.skin_care.escape.v1")):
    pass
```

The walk that finds these is deliberately generic — it follows every AST child,
not only the ones that are themselves expressions, because Python's grammar hangs
expressions off semantic wrappers (`ast.keyword`, `ast.comprehension`,
`ast.arguments`, `ast.arg`, `ast.match_case`, `ast.withitem`,
`ast.ExceptHandler`) that a type-filtered walk treats as dead ends. It prunes at
three places and only three: nested statements (the statement walker owns those),
a function's or class's body, and a lambda's body. A comprehension is not pruned,
because a walrus inside one binds in the enclosing scope.

This layer has to be complete rather than convenient. The failure it prevents is
not a wrong answer but *no* answer: `PACK_ID, other = ("…", 1)` genuinely binds
`PACK_ID`, and a collector that only recognised `NAME = …` would classify the file
as infrastructure and drop a governed pack out of the inventory in silence.

Bindings inside a nested *body* — a function, an async function, a class body, a
lambda body — are not module bindings and are not counted. A `PACK_ID` local to a
helper, or assigned in a class body, does not make the file a pack.

**Layer two accepts two forms.** For a descriptor field, exactly one module-scope
binding, written as `NAME = "literal"` or `NAME: str = "literal"`. For the
compiler, exactly one plain top-level `def`. Everything else fails closed:

| Situation | Finding |
| --- | --- |
| Value cannot be read without running the module — a call, an f-string, a name, a destructuring target, a loop target, an import, a bare `NAME: str`, a conditional declaration | `NON_STATIC_METADATA` |
| The name is touched more than once at module scope — a second declaration, a rebinding, a `del` | `DUPLICATE_DECLARATION` |
| No module-scope binding at all | `MISSING_DOMAIN` / `MISSING_CATEGORY` / `MISSING_REASON_KEY`, or `MISSING_COMPILER`; for `PACK_ID`, the file is simply not a pack |
| The compiler's last module-scope binding is not a plain `def` — a class, an import, a shadowing assignment, a `del`, or a coroutine (Step 8H calls it synchronously) | `COMPILER_NOT_A_FUNCTION` |

A stale earlier `def` is never reported as the valid compiler when a later
module-scope statement would replace or remove it: whatever binds last is what the
release workflow would reach for. The function is named, never called — compiling
requires a reviewed published evidence entry, which an inventory tool has no
business inventing.

Any module-scope attempt to bind `PACK_ID`, in any of those forms, makes the file a
governed-pack candidate. That is deliberate: a pack whose identity cannot be read
must appear in the report as broken, never disappear as though it were never
written.

**A filename is never an exemption.** Every `.py` file in the pack directory is
inspected except two named outright: `__init__.py` and `inspection.py`. There is
no rule about leading underscores or any other prefix, because such a rule would
mean a governed pack could leave the inventory by being renamed `_hidden_pack.py`.
A file that simply does not declare `PACK_ID` is not a governed pack and is not an
error.

**Nothing else is touched.** No database connection, no network call, no
credential, no environment variable — running the command with the production
configuration absent, empty or deliberately wrong produces byte-identical output,
and a test holds that. It never compiles a manifest, never prepares, publishes,
activates, deactivates or rolls back a release, and never evaluates a customer
decision. Inventory is not activation, and the boundary described above is
unchanged by it.

**A pack stays inert.** Discovery lives in `inspection.py`, not in the package's
`__init__.py`, which remains a docstring and imports nothing. Importing
`app.knowledge_packs` still loads no pack; importing the inspector still loads no
pack; importing the application still loads neither. The inspector finds its own
directory through `__file__` and names it through `__package__` rather than
spelling the dotted path, so the standing rule that no module under `app/` may
name `app.knowledge_packs` — the rule that stops an accidental runtime import —
needs no exception for it.

**What a descriptor holds.** Module, `PACK_ID`, `DOMAIN`, `CATEGORY`, `REASON_KEY`
and the compiler's name. Deliberately no evidence summary, source locator, fact
condition or strength: those belong to the reviewed evidence record, and a
cross-pack listing must not become a second, unreviewed copy of them. Future hair,
cosmetics or food packs will not share the scientific shape of this one, and the
inventory does not ask them to.

**Two packs may not share a reason key.** That would put two reviewed knowledge
claims in competition for the same sentence a customer reads, with nothing in the
release manifest to say which wins. It is rejected by default. If shared ownership
is ever wanted, that is a reviewed change to the rule, not something an inventory
tool should quietly permit.
