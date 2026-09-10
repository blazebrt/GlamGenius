# The glycerin × dry-skin knowledge pack

The second reviewed Skin-Care knowledge specification, and the first evidence
that the multi-pack architecture is real rather than asserted. One pack proves
nothing about scale; two packs that keep their identities apart, compile
through the same generic tool, and never execute each other, do.

`docs/architecture/FIRST_PRODUCTION_KNOWLEDGE_PACK.md` describes the shape all
of this follows. This document covers only what is specific to glycerin, plus
the two-pack proof.

## The exact proposition

> For a user who reports that their skin often feels dry or tight, glycerin is
> a supporting Skin-Care ingredient-level signal.

That is the whole of it. It is **ingredient-level**, **non-medical**, **not
concentration-specific**, not whole-formula approval, not treatment, and not
safety certification.

The user context is the ordinary care declaration the profile already
collects — `care_skin_usual_feel = often_dry_or_tight`, the answer to "my skin
often feels dry or tight". No profile attribute was added, and no diagnosis,
symptom or free text is involved anywhere.

## Identity: one substance, one key

Glycerin and glycerol are the same chemically defined substance. Treating them
as two ingredients would be the beginning of exactly the failure the substance
identity architecture exists to prevent, so the pack declares one identity.

**The canonical key is `glycerin`, and that was not a fresh decision.** This
repository already canonicalises this substance: `app/domains/routines/ontology.py`
declares the ingredient under the key `glycerin`, with `glycerol` and
`glycerine` recorded as its aliases and its INCI name given as "Glycerin". The
first governed pack follows the same convention — its `SUBSTANCE_KEY` is
`petrolatum`, the key the ontology uses too. Choosing `glycerol` here would
have contradicted a decision the repository had already made.

A test asserts all of this against the live ontology, including that `glycerol`
does **not** exist there as a separate ingredient. If that ever changes, the
pack's identity claim fails loudly rather than drifting.

| Field | Value |
| --- | --- |
| `SUBSTANCE_KEY` | `glycerin` |
| `IDENTITY_ENTITY_KIND` | `defined_substance` |
| `IDENTITY_NAME` | Glycerin |
| `IDENTITY_NAME_NAMESPACE` | `official_reference` |
| `IDENTITY_NAME_PREFERRED` | true |
| `IDENTITY_SOURCE_TYPE` | `government_reference` |
| `IDENTITY_SOURCE_TITLE` | Glycerol |
| `IDENTITY_SOURCE_PUBLISHER` | PubChem |
| `IDENTITY_SOURCE_EXTERNAL_ID` | 753 |
| `IDENTITY_CAS_NUMBER` | 56-81-5 |

Two of those need explaining.

**`defined_substance`, where petrolatum is a `mixture`.** Glycerol is one
molecule; petrolatum is not. This is the one identity field where the two packs
legitimately differ, and it differs because the chemistry differs.

**`IDENTITY_NAME` is Glycerin while `IDENTITY_SOURCE_TITLE` is Glycerol.**
These are not in conflict. In this repository `is_preferred` marks the one name
GlamGenius treats as *the entity's* preferred name —
`SubstanceIdentity.preferred` guarantees exactly one per entity — not a claim
about which title the source prefers. The reference record's own title is
recorded separately, and it is "Glycerol". The first pack models the same
distinction: its preferred name is "Petrolatum" while its source title is
"Petrolatum [USP]". `official_reference` means "a name as printed in an
official register or reference work", which "Glycerin" is in that record.

No INCI authority is claimed. The pack declares the reference record it rests
on and nothing else; a test asserts no `IDENTITY_INCI*` field exists.

> **Verification status.** The PubChem values above were supplied as reviewed
> inputs to this milestone. They could **not** be re-verified against the live
> record from the environment this branch was built in: outbound network access
> there is restricted to GitHub, and `pubchem.ncbi.nlm.nih.gov` — like
> `pubmed.ncbi.nlm.nih.gov` and `www.aad.org` — is refused by the egress proxy.
> Nothing was inferred to fill the gap, and no field was invented; but the
> identity metadata rests on the reviewer's authority, not on a check performed
> here, and should be confirmed against CID 753 before this pack is approved.

## Evidence path 1 — dermatologist guidance

The current American Academy of Dermatology Association dry-skin guidance lists
glycerin among the ingredients to look for in a cream or ointment for dry skin.
This is the **same reviewed page and the same locator** the petrolatum pack
pins, because that one locator lists both ingredients.

The metadata policy is therefore also the same, and deliberately so: the page
reports a last-updated value and states no publication date, so
`publication_date` stays null and the update is recorded as a version instead.
Neither reviewed source states a territory, so `jurisdiction` is null. Optional
provenance is left absent where the source does not establish it rather than
inferred into something that looks authoritative.

The values are **copied into this pack as governed literals, not imported**.
The Step 14C rule is that a governed pack may not statically import another
governed pack, and reaching into the first pack for its constants would make
selecting one pack execute two. A test proves the copies are faithful by
reading the other pack's *source text* — so fidelity is checked without either
pack depending on the other.

## Evidence path 2 — randomized research

The reviewed clinical path is the four-arm, randomized, double-blind crossover
study in healthy volunteers with dry skin (PMID 31532576, DOI
10.1111/jocd.13163) — again the same study the first pack cites, because it
reports on both components.

The reviewed reading taken here is narrow:

> the glycerol component improved skin hydration

It is **not** read as: treats dry skin, repairs all damaged barriers, prevents
disease, works at every concentration, works in every formulation, or is
superior to all moisturizers. The study was explicitly not designed to evaluate
therapeutic benefit.

The PubMed record is the citation location; the publisher is Wiley Periodicals,
Inc., because the article carries that copyright. Naming the database as the
publisher would misattribute the work.

## Why `moderate`, not `strong`

- current dermatologist guidance explicitly includes glycerin among ingredients
  to look for in a cream or ointment for dry skin;
- randomized dry-skin research found the glycerol component improved skin
  hydration;
- but the evidence is **ingredient/component-level**, not an exact-product
  therapeutic trial;
- it establishes **no concentration**;
- it does not speak for **every commercial formula**;
- and the randomized study was not a therapeutic-benefit trial.

## The selected-citation boundary

This is the part most easily got wrong, and it has its own guard.

The customer-facing reason is attached to **one** selected citation, and that
citation is the AAD locator. Step 8F requires the reason and its source to say
the same thing. So the reason may say:

> For dry skin, dermatologist guidance includes glycerin among ingredients to
> look for in a cream or ointment.

and it may **not** say: improves skin hydration, increases hydration, repairs
the barrier, reduces TEWL, treats dry skin, heals dry skin, clinically proven
for you, safe, safe for everyone, recommended for everyone, or better than
another ingredient.

The hydration finding is the one to watch, because it is the tempting one. It
is true, it is reviewed, and it is *why this pack's strength is moderate* — but
it belongs to the study, and the study is not the citation the customer is
shown. Borrowing it to make the dermatologist-guidance sentence sound stronger
is precisely the failure `REASON_CLAIMS_OUT_OF_SCOPE` exists to catch. The
finding stays in the evidence summary and the strength rationale, where it
belongs; a test asserts it is in both and absent from the reason intent.

**No customer copy is wired in this milestone.** `FUTURE_REASON_INTENT` records
the reviewed intent so its scope is reviewable and testable before any string
exists. `backend/app/content/for_you_copy.py` is untouched. When copy is
written it will live in a keyed string file like every other user-facing
string.

## The decision rules

| | |
| --- | --- |
| Semantic | `for_you.semantic.skin_care.glycerin.dry_skin` v1, signal `supporting` |
| Policy | `for_you.policy.skin_care.glycerin.dry_skin.buy` v1, `supporting_only` → `buy` |
| Explanation | `for_you.explanation.skin_care.glycerin.dry_skin.buy` v1 |
| Reason key | `for_you.skin_care.glycerin.dry_skin.dermatologist_guidance` |

The `buy` is **this pack's reviewed policy**, bound to this pack's one semantic
rule. It does not establish `supporting → BUY` for any other ingredient, and
there is no such algorithm anywhere: a test asserts the signal and the action
are authored constants and that nothing in the module branches on either name
to arrive at an action.

All existing Step 8E/8H structural prerequisites remain authoritative. A
formula carrying an unresolved, ambiguous or unmapped co-ingredient must still
withhold a confident personal verdict — `Glycerin, <something unknown>` does
not inherit the action.

## The two-pack proof

This is the architectural point of the milestone.

**Inventory.** `python scripts/inspect_knowledge_packs.py --json` reports
`pack_count: 2`, `errors: []`, exit 0 — two distinct `PACK_ID`s, two distinct
`REASON_KEY`s, both declaring a valid compiler.
`backend/app/knowledge_packs/inspection.py` was **not modified** to make the
second pack pass.

**Generic compilation.** The merged Step 14C builder compiles both packs by
their exact `--pack-id`. It needed **no change at all** to recognise glycerin —
a test asserts its executable source names neither pack, neither pack id, and
no pack module, and calls `import_module` exactly once. Had it required a
pack-specific change, Step 14C would not have been generic and this milestone
would have proved the opposite of what it claims.

**Isolation.** A meta-path recorder in a fresh interpreter shows what the
import system was actually asked for:

| Selection | Modules imported |
| --- | --- |
| glycerin's exact id | `glycerin_dry_skin_v1` only |
| petrolatum's exact id | `petrolatum_dry_skin_v1` only |
| an unknown id | none |

**Mutual refusal.** Neither pack will compile the other's reviewed evidence
entry. Both directions are tested.

**Petrolatum is unchanged.** Its file was not touched and its manifest still
compiles to `be1fbbf8ae6435e18fdcfba86d85d7697a6257959bc2c6c75c452bcd434e75be`,
byte-identical between the generic and the legacy builder.

## What this pack does not do

It is inert source. It performs no database read or write, no network call, no
file write and no environment lookup; it registers nothing, is imported by no
application startup path, and importing the package root still imports no pack.
It is not a runtime registry, not a seed, not a startup hook, not API
behaviour, and not automatic activation.

**Nothing here is activated.** Compiling a manifest offline and putting one in
front of customers are different acts. No release row is written, no evidence
is authored or published, no Phase B operation is performed, and
`scripts/operate_step8i_petrolatum_release.py` was not generalised. Production
activation remains manual, one pack at a time, through the existing governed
lifecycle — and this pack has not entered it.
