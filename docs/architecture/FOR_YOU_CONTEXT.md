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
