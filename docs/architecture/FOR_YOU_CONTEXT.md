# FOR YOU personal context foundation

## Step 8A boundary

Step 8A answers one narrow question:

> Given one account, one explicit product category, and safety context a caller
> already holds, which existing trusted non-medical personal facts may a future
> FOR YOU engine consider?

It produces a live, immutable read projection. It is not a personal score, a
product score, a verdict, a recommendation, a safety classification, or an
evidence-applicability answer. There is no arithmetic, weighting, grade,
ranking, alternative selection, or `BUY` / `WAIT` / `SKIP` behavior.

The intended layering is:

```text
LabelSnapshot
    ↓
Step 7B / 7B.1 — formula and canonical identity
    ↓
Step 7C — category-specific published reference knowledge
    ↓
Step 8A — trusted personal context
    ↓
Step 8B — future personal evidence applicability
```

Step 8A does not consume Step 7C. It deliberately defines the same explicit
category strings (`packaged_food`, `skin_care`, `hair_care`, and `cosmetics`)
as local interoperability vocabulary, not as an authority dependency. A future
integration must perform an explicit strict conversion between the categories.

## Trust and category allowlists

Profile remains the only storage and read authority. A Profile attribute is
trusted only when its source is exactly `user_declared` and its verification
state is exactly `confirmed`. Confidence cannot override either requirement.
Photo observations, inventory or behaviour inference, integrations, stylist
verification, and unverified, rejected, or superseded values are excluded.

The allowlist is closed and category-specific:

| Category | Body facts |
| --- | --- |
| Skin Care | `care_skin_usual_feel`, `care_skin_sensitivity` |
| Cosmetics | `care_skin_usual_feel`, `care_skin_sensitivity` |
| Hair Care | `care_hair_pattern`, `care_hair_strand_characteristic`, `care_hair_density`, `care_hair_wash_frequency`, `care_hair_processing`, `care_heat_styling_frequency`, `care_scalp_usual_feel`, `care_humidity_frizz_sensitivity` |
| Packaged food | none in V1 |

Packaged food intentionally returns `not_enough_personal_context`. The existing
Profile has no governed, non-medical food-personalisation vocabulary that this
layer may treat as relevant to body-effect decisions. Sleep, hydration, stress,
workout frequency, and activity level do not fill that gap.

`care_fragrance_preference` and `care_routine_effort` may be projected for Skin
Care, Hair Care, and Cosmetics, but only into a structurally separate
`preference_facts` collection. A preference never becomes body-effect evidence,
and preference completeness never changes body-context status.

Appearance and style data are excluded even when confirmed and user-declared.
That includes skin tone, undertone, face shape, style and colour preferences,
fit, silhouettes, height for appearance, experimentation, and appearance goals.
Allergies are also excluded from V1: this layer performs no allergy matching,
warning, or scoring. Pregnancy, breastfeeding, medication, diagnosed
conditions, disease, child health, treatment, prescriptions, dosage, and
symptoms are not Profile inputs to this layer.

An explicit `not_sure` remains visible as an immutable provenance fact marked
`explicit_unknown`. It is also reported with the controlled
`explicit_unknown` missing reason and never counts as usable body context.
Other missing reasons distinguish absence, an untrusted source, and a value
that was not confirmed. Facts and missing rows follow allowlist order.

## Hard handoff before personal reads

The existing `app.domains.routines.hard_handoff.evaluate` function is the sole
medical-boundary authority. Step 8A calls it before looking up a Profile. Age
under 12, a child subject, pregnancy, breastfeeding, medication, a clinical
condition, or uncertain medical text returns `handoff_required` with the
existing reason and message. That path performs zero Profile or attribute
queries and returns no facts, missing rows, profile ID, or profile version.

`PersonalLensSafetyInput.text` does not create a product text field. It only
lets a future caller pass context already in memory through the constitutional
gate. Step 8A never stores, returns, or logs that text. It exposes no API and
adds no database column.

## Live, bounded, and read-only

An absent AppearanceProfile is normal. Step 8A does not create one and returns
`not_enough_personal_context`. With a profile, it returns the profile ID and
version so a future result can state exactly which context version was
consulted. Updating Profile through its existing authority changes the next
projection and increments that provenance version; Step 8A stores neither the
old nor the new projection because Profile change events already own history.

The hard-handoff path uses zero database queries. An account without a Profile
uses one Profile query. An existing Profile uses that query plus one bounded
attribute query. There is no query per field and no insert, update, delete,
flush, commit, cache, model, table, migration, or materialised context.

Step 8A has no product, barcode, LabelSnapshot, formula, ingredient, substance,
brand, Open Food Facts, network, AI, family, subscription, payment, entitlement,
or frontend input. Step 8B will be the first reviewed layer allowed to join
this personal context with governed product evidence.

## Step 8B governed evidence applicability

Step 8B answers one additional, still deliberately bounded question:

> Given an exact Step 7C formula interpretation and an exact Step 8A context
> for the same explicit category, which reviewed, published, non-medical
> substance-personal-applicability claims exactly match the trusted body facts?

It consumes both earlier projections. It does not read Profile storage,
re-tokenise a formula, reopen canonical identity, derive meaning from a Step 7C
reference-role summary, or infer a claim from ingredient presence. A valid
`substance_category_interpretation` claim such as a reference role is never a
personal rule. Only the separate controlled claim type
`substance_personal_applicability` can enter this layer.

### Strict structured applicability

Machine applicability lives in the claim's `structured_value` under this exact
V1 block:

```json
{
  "substance_personal_applicability": {
    "schema_version": "1",
    "category": "skin_care",
    "all_of": [
      {
        "fact_key": "care_skin_sensitivity",
        "operator": "equals_any",
        "values": ["sometimes_reactive", "often_reactive"]
      }
    ]
  }
}
```

`all_of` contains one to four conditions and every condition must match. Scalar
Profile facts use exact `equals_any`; list-valued facts use exact
`contains_any`. There is no `any_of`, negation, nesting, numeric comparison,
regex, substring, alias, case-fold, fuzzy match, or prose inference. Fact keys
must be in Step 8A's body allowlist for the same category, operators must match
the canonical Profile fact shape, and values must be exact canonical registry
choices. Preferences and `not_sure` are invalid claim conditions. Duplicate
values, duplicate fact conditions, unknown keys, unknown operators, malformed
values and unsupported schema versions fail closed.

Packaged food remains intentionally empty in V1 because Step 8A has no governed
packaged-food body facts. Lifestyle, wellness, appearance, allergy and medical
Profile fields cannot fill that gap.

### Public evidence gate

An applicable claim must use the exact resolved substance key, requested
evidence domain, new claim type, category and V1 payload. It must pass the
shared published public-knowledge gate, be human-reviewed and published with no
unresolved verification doubt, be non-AI, supported, clinically studied, and
have strong, moderate or limited evidence strength. At least one fully reviewed
supporting path must point to an active, named, openable source with a recorded
use note. V1 admits only official guidelines, government references, systematic
reviews, peer-reviewed research and professional consensus. Manufacturer
materials, ingredient databases, traditional references, background links and
generic other sources cannot carry personal body-effect applicability.

The category-to-domain mapping is exact: packaged food to nutrition, Skin Care
to skin care, Hair Care to hair care, and Cosmetics to cosmetics. Cosmetics is
never silently treated as Skin Care.

### Handoff, identity and absence

The top-level snapshot helper invokes Step 8A first. A medical handoff stops
before Step 7C or Step 8B evidence reads and returns only the existing safe
handoff projection; the input text is never stored or returned. No usable body
context also stops before product-evidence work.

Only a Step 7C `resolved` identity may be queried by exact canonical key.
Unresolved names stay unresolved. Ambiguous identities retain every candidate,
but no candidate is queried or selected and personal context cannot break the
tie. Duplicate printed positions remain duplicate output rows while sharing one
batched evidence load.

A non-matching claim is simply inapplicable, not contradictory. No match and no
claim both mean `not_enough_information`; neither implies good, bad, safe,
unsafe, suitable or unsuitable.

### Live, bounded and non-decisional

For K distinct resolved keys, Step 8B performs at most one candidate-claim
query and, only when candidates exist, one source-path query. It does not query
per ingredient, ambiguous candidate or raw printed name. Results retain exact
LabelSnapshot provenance, explicit category, formula status, Profile ID and
version, identity state, matched Profile-attribute IDs, exact reviewed claim
text and complete source provenance.

The projection is immutable and live. It adds no result model, table, cache,
event, API, frontend, production knowledge seed or write. Its sole schema
change extends the existing evidence claim-type CHECK vocabulary. It produces
no score, grade, confidence number, ranking, concentration estimate,
alternative, recommendation or `BUY` / `WAIT` / `SKIP` verdict.
