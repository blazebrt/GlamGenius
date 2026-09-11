# The Skin-Care V1 knowledge roster

**Step 14 is complete when this exact five-pack roster passes independent review. There is no Step 14F.**

Everything outside this roster is Knowledge Operations: ongoing work with its own review, not another Product Finalization substep.

---

## What this roster is, and is not

It is the **governed V1 decision seed corpus** — the small, reviewed set of ingredient-level propositions the Skin-Care decision engine is allowed to act on at launch.

It is **not the entire universe of skin-care knowledge**, and nothing here should be read as claiming otherwise. Five packs is a seed. Most ingredients on most labels are not in it, and for those the honest answer remains that there is not enough information.

That is a design outcome, not a gap:

- `NOT_ENOUGH_INFORMATION` — the system was not entitled to decide.
- `NOT_ENOUGH_DECISION_POLICY` — no reviewed policy covers this exact governed state.

Both remain legitimate, governed results. Step 14E does not weaken any structural prerequisite to make the product look more decisive.

---

## The five packs

| # | Substance | Profile condition(s) | Signal | Action | Primary (selected) source | Strength | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `petrolatum` | `care_skin_usual_feel = often_dry_or_tight` | supporting | BUY | AAD, *Dermatologists' top tips for relieving dry skin* | moderate | merged, frozen |
| 2 | `glycerin` | `care_skin_usual_feel = often_dry_or_tight` | supporting | BUY | AAD, *Dermatologists' top tips for relieving dry skin* | moderate | merged, frozen |
| 3 | `fragrance` | `care_skin_usual_feel = often_dry_or_tight` | cautionary | SKIP | AAD, *Dermatologists' top tips for relieving dry skin* | limited | new in 14E |
| 4 | `retinol` | `care_skin_usual_feel = often_dry_or_tight` | cautionary | SKIP | AAD, *Retinoid or retinol?* | moderate | new in 14E |
| 5 | `salicylic_acid` | `care_skin_usual_feel = often_oily` **and** `care_skin_sensitivity = rarely_reactive` | supporting | BUY | AAD, *How to control oily skin* | moderate | new in 14E |

Between them the roster exercises both signal directions, both actions, both skin-feel contexts, one- and two-fact applicability, a mixture identity and three defined-substance identities, and professional, government and peer-reviewed source types. That coverage is the point: a roster that only ever said BUY would not have tested the machinery.

**No new profile key was added.** Every condition uses an existing controlled care declaration — `care_skin_usual_feel` and `care_skin_sensitivity` — with values already in the registry.

---

## Notes on the three new packs

### Fragrance — a mixture, and only a dry-skin caution

Fragrance is modelled as a `mixture`, not a defined substance: a fragrance formula can be a complex mixture of natural and synthetic ingredients that a label may declare as the single word "Fragrance". There is deliberately **no CAS number and no external numeric identifier**, because an id would assert a discrete chemical identity that does not exist. The name namespace is `common`, not `inci` — the FDA describes the English label word as the ingredient's common or usual name, and this pack claims no INCI register authority.

Strength is `limited`, not moderate. The dry-skin direction rests entirely on current professional guidance recommending fragrance-free products for dry skin. **No population sensitisation or allergy literature is relied on**, and the pack asserts no allergy, no sensitivity diagnosis, and nothing about safety.

`care_fragrance_preference` is a *preference* field. It is not used here and must never be turned into a scientific claim: "prefers fragrance-free" is not "fragrance is medically unsuitable".

### Retinol — the pregnancy boundary stays upstream

Identity stays exactly `retinol`. Retinal, retinyl palmitate, tretinoin and adapalene are separate substances and this pack speaks for none of them. The retinoid family relationship is recorded because it is *why* guidance about retinoids reaches retinol — never as a claim that family members are interchangeable.

The reviewed AAD page also discusses pregnancy. **That boundary lives in `app/domains/routines/hard_handoff.py` and stays there.** This pack does not duplicate, inspect, infer or reinterpret it, and the customer reason may not mention it. The guard on the reason lists pregnancy first for exactly that reason: the risk is leakage from a source into a sentence, not invention from nowhere.

### Salicylic acid — why two conditions

The reviewed guidance says two things in one breath: ingredients such as salicylic acid can help reduce oiliness, *and* they may be too harsh for some skin. A pack keyed only on oily skin would carry the first half and drop the second.

So the applicability is narrower than "salicylic acid is good for oily skin": oily skin **and** a reported sensitivity of `rarely_reactive`. Both are mandatory.

**That second condition is a conservative eligibility boundary, not a scientific finding.** Nothing claims `rarely_reactive` guarantees anyone will tolerate anything. It exists so this launch pack does not reach `sometimes_reactive`, `often_reactive` or `not_sure` users while the same guidance warns the ingredient can be too harsh. For those users, not enough information is the correct answer for now — and the customer reason may never turn the boundary into a reassurance.

---

## The engine still refuses to guess

The policy engine matches **exact** semantic rule identities and versions, not signal direction. Each of the five reviewed policies targets exactly one semantic rule.

So a product containing two of these substances presents a governed state no reviewed policy covers, and the answer is `NOT_ENOUGH_DECISION_POLICY`. A gap flag — an unresolved or ambiguous co-ingredient, a personal-evidence gap — likewise changes the target and loses the match.

**This is a safety property, not a defect, and Step 14E does not fix it.** None of the following exist anywhere in this repository, and tests assert their absence:

- `supporting → BUY` or `cautionary → SKIP` as a general rule
- cautionary overriding supporting
- majority vote, score, weight, confidence threshold
- a fallback or wildcard policy
- "unknown ingredient means neutral", "missing evidence means safe"

Each would be a new global product policy. Any of them would need its own review, and none belongs in a knowledge-roster milestone.

---

## Source verification status

The independent reviewer opened and verified the public sources for this milestone. The build environment for this branch **cannot** reach them — outbound access is restricted to GitHub, and `www.aad.org`, `www.fda.gov`, `pubmed.ncbi.nlm.nih.gov` and `pubchem.ncbi.nlm.nih.gov` are all refused at CONNECT.

So, precisely:

- **Titles, locators, last-updated dates, PMIDs, DOIs, CAS numbers and PubChem CIDs** came from the independent reviewer, not from this environment.
- **Canonical URLs for four pages** — AAD *Retinoid or retinol?*, AAD *How to control oily skin*, FDA *Fragrances in Cosmetics*, FDA *Cosmetic Ingredient Names* — were **constructed from each site's canonical path convention and are NOT verified**. They must be confirmed at review.
- **PubChem and PubMed URLs** were derived from the canonical patterns already reviewed and merged in the petrolatum and glycerin packs, using the reviewer-supplied CIDs and PMIDs.
- **The AAD dry-skin URL** used by the fragrance pack is copied verbatim from the two merged packs, where it is already reviewed.

Nothing was inferred beyond that, and no metadata field was invented to fill a gap.

---

## Knowledge Operations backlog — deliberately outside Step 14

These are deferred with reasons, not forgotten. Each needs its own review; none is a Product Finalization blocker.

### Identity debt

| Candidate | Why deferred |
| --- | --- |
| hyaluronic acid / sodium hyaluronate | The ontology currently aliases these together. Identity normalization may need to distinguish acid and salt forms before a governed pack relies on either. |
| dimethicone | Polymeric material identity deserves its own review rather than pretending there is one simple discrete-molecule CID. |

### Concentration and formulation sensitive

| Candidate | Why deferred |
| --- | --- |
| glycolic acid | Effect and tolerance depend heavily on concentration and pH; needs formulation review. |
| lactic acid | Same. |
| urea | Behaves as a humectant at low levels and a keratolytic at high ones — a single ingredient-level signal would be misleading. |

### Concern- or goal-dependent

niacinamide · azelaic acid · benzoyl peroxide · vitamin C · retinoids for specific goals

Skin-Care personalization deliberately does **not** introduce acne, pigmentation, rosacea, eczema or anti-ageing as new medical or cosmetic-concern facts. Adding these ingredients well would require that model, and that model is a separate decision.

### Regulated product context

zinc oxide · titanium dioxide · other sunscreen filters

These need product-level SPF and regulatory context. Reducing a sunscreen filter to an ingredient-level personalized BUY signal would be wrong.

### Additional reactivity states

`sometimes_reactive` and `often_reactive` remain governed profile facts. Step 14E does not invent evidence paths merely to fill every cell of a matrix; where no reviewed evidence path exists, not enough information is the answer.

---

## What this milestone did not touch

No production access of any kind, and no activation. `scripts/operate_step8i_petrolatum_release.py` was not generalised and no bulk or portfolio activation exists. No evidence row, no release row, no Phase B operation. No customer copy: the three new packs carry a reviewed *future reason intent* only, and `backend/app/content/for_you_copy.py` is untouched. No migration, no dependency, no policy-engine change, no medical-handoff change.

`backend/app/knowledge_packs/inspection.py` and `scripts/build_knowledge_pack_release.py` are both unmodified — the generic compiler needed nothing said about any of the three new packs, which is the property Step 14C was built to have.

**Merging this does not authorize a production deployment from any SHA.** The separate Product Finalization Step 8 production provisioning authority remains `db0872194e305867084f5ef39f002e29259f8eb5`.
