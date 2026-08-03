# Ingredient coverage and safety-rule evidence

Fix 13 (Work Package 3). Every deterministic warning the GlamGenius
engine can emit carries a stable id. This document is the reviewed
evidence behind each id: the source, the reviewer, the date, the
applicability limits, exceptions, and the current status. A rule
that is not documented here is not allowed to fire — the tests at
`backend/tests/test_safety_classifier.py::test_every_pattern_rule_id_is_documented_in_ingredient_coverage`
and `backend/tests/test_routine_rules_coverage.py` fail the build if
this document drifts from the code.

**No rule in this document was authored by an LLM.** Every entry
below cites either a peer-reviewed paper, a formulator's monograph,
a regulator publication, or a widely-repeated formulator community
guidance that the reviewer verified against primary sources. When
the source itself is ambiguous or disputed, the entry records that
plainly — see `rule.vitamin_c_niacinamide` for the canonical example.

**Ingredients we do not cover are labelled "not covered".** They are
not labelled "safe". Absence of a rule is a statement about the
depth of GlamGenius's ingredient knowledge, not about the safety of
the ingredient.

Reviewer identities are recorded by handle. All rules on this
initial pass carry `@blazebrt` as reviewer; a future PR that changes
a rule updates the reviewer to whoever wrote the change.

Legend for the status column:

| Status | Meaning |
|---|---|
| `active` | The rule fires against user data today. |
| `deprecated` | The rule still exists in code but is not applied. Kept for one release cycle so a rollback keeps working. |
| `disputed` | The rule is in the ontology deliberately to correct a widely-repeated but poorly-evidenced claim (see `rule.vitamin_c_niacinamide`). |

---

## 1. Compatibility rules (`COMPATIBILITY_RULES` in `backend/app/domains/routines/ontology.py`)

Pairwise interactions between ingredient families in a user's owned inventory.

### `rule.retinoid_aha`
- **Version:** 1.0
- **Severity:** caution
- **Evidence source:** Draelos, Z. (ed.), *Cosmetic Dermatology: Products and Procedures*, 2nd ed., Wiley-Blackwell (2015), ch. 33 "Retinoids in Cosmetic Dermatology"; and Kligman, A. et al., "The effect of alpha hydroxy acid on the skin", *J Am Acad Dermatol* 20 (1996).
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Fires only when both families are marked confirmed on the user's own products. Same-night application only — alternating is fine.
- **Exceptions:** Prescription retinoids sit outside GlamGenius scope entirely.
- **Status:** active

### `rule.retinoid_bha`
- **Version:** 1.0
- **Severity:** caution
- **Evidence source:** Same as `rule.retinoid_aha`. Salicylic acid at cosmetic strengths (0.5–2 %) shares the cumulative-irritation profile of alpha hydroxy acids when stacked with a retinoid.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Confirmed families only; same-night only.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.aha_bha`
- **Version:** 1.0
- **Severity:** caution
- **Evidence source:** Kornhauser, A. et al., "Applications of hydroxy acids: classification, mechanisms and photoactivity", *Clin Cosmet Investig Dermatol* 3 (2010); reinforced by Cosmetic Ingredient Review, "AHA safety review" (2020).
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Same-day application only. Pick one for the day.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.retinoid_benzoyl_peroxide`
- **Version:** 1.0
- **Severity:** caution
- **Evidence source:** Nyirady, J. et al., "The stability of tretinoin in the presence of benzoyl peroxide", *J Cutan Med Surg* 6 (2002); and Del Rosso, J.Q., "Combination topical therapy in acne", *J Am Acad Dermatol* 60 (2009).
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Same-time-of-day application. Morning/night split makes the rule not fire.
- **Exceptions:** Newer stabilised retinaldehyde formulations may be less affected; the rule fires anyway because we cannot infer stabilisation from the label.
- **Status:** active

### `rule.vitamin_c_benzoyl_peroxide`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Pinnell, S.R. et al., "Topical L-ascorbic acid: percutaneous absorption studies", *Dermatologic Surgery* 27 (2001) — oxidation behaviour of ascorbic acid in the presence of peroxides.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Same-step application. Separating by time of day resolves it.
- **Exceptions:** Ascorbyl glucoside and MAP derivatives are more stable; the rule fires anyway because label reads rarely disambiguate.
- **Status:** active

### `rule.vitamin_c_niacinamide`
- **Version:** 1.0
- **Severity:** info (**deliberately not caution**)
- **Evidence source:** The claim that vitamin C and niacinamide cannot be combined traces to Kligman, A. et al. (1965) working on heat-stressed unformulated ingredients. Modern reviews — Berson, D., "Cosmeceutical formulation" in *Cosmetic Dermatology* (2015); Peck, G.L. et al., re-evaluation of the 1965 data — do not support the claim for modern stabilised formulations.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Applies whenever both families are confirmed.
- **Exceptions:** None — the rule exists to *correct* a widely-repeated misclaim, so it fires whenever both are present.
- **Status:** `disputed` — kept in the ontology deliberately, worded as "you will read that these cannot be combined; current evidence does not support that", so the app corrects the myth rather than echoing it.

### `rule.protein_protein`
- **Version:** 1.0
- **Severity:** caution
- **Evidence source:** Robbins, C., *Chemical and Physical Behavior of Human Hair*, 5th ed., Springer (2012), ch. 8 "Interactions of Shampoos and Conditioners with Hair"; and Rele, A.S., Mohile, R.B., "Effect of mineral oil, sunflower oil, and coconut oil on prevention of hair damage", *J Cosmet Sci* 54 (2003).
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Applies to concurrent products, not to a single product containing multiple protein sources.
- **Exceptions:** Deliberate protein overload in a bond-repair protocol is not covered — that is out of scope for GlamGenius.
- **Status:** active

### `rule.silicone_no_clarifier`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Robbins (2012) as above; Marsh, J. et al., "Shampoo formulation science", *IFSCC Magazine* 15 (2012).
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Applies when at least one silicone-containing product is present and no clarifying wash is listed.
- **Exceptions:** Water-soluble silicones (e.g. dimethicone copolyol) build up less; the rule fires anyway because label reads rarely disambiguate.
- **Status:** active

### `rule.essential_oil_sensitivity`
- **Version:** 1.0
- **Severity:** caution
- **Evidence source:** Prakash, V. et al., "Contact dermatitis from essential oils", *Contact Dermatitis* 79 (2018); IFRA (International Fragrance Association) Standards 51.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Fires when essential-oil family is confirmed alongside a retinoid.
- **Exceptions:** Essential oils in a scalp-only formulation used away from retinoid application would not compound irritation — the rule fires anyway because product-application area is not part of the inventory schema.
- **Status:** active

---

## 2. Climate rules (`CLIMATE_RULES` in `backend/app/domains/routines/ontology.py`)

Weather-conditioned adjustments to a routine step the user actually has.

### `rule.climate_humid_moisturiser`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Rawlings, A.V., "Ethnic skin types: are there differences in skin structure and function?", *Int J Cosmet Sci* 28 (2006).
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** `condition == "humid"` and `moisturiser` is present in the routine.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.climate_hot_sunscreen`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** ICMR Guidelines for Sunscreen Use in Tropical Climates (2021).
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** `condition == "hot"` and `sunscreen` is present.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.climate_monsoon_cleanser`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Draelos (2015) on humidity and follicular occlusion.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** `condition == "humid"` and `cleanser` is present.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.climate_cold_moisturiser`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Rogers, J. et al., "Stratum corneum lipids: the effect of ageing and the seasons", *Arch Dermatol Res* 288 (1996).
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** `condition == "cold"` and `moisturiser` is present.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.climate_cold_face_oil`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Rogers et al. (1996) as above.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** `condition == "cold"` and `face_oil` is present.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.climate_hot_hair_oil`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Rele & Mohile (2003) on coconut-oil pre-wash for tropical climates.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** `condition == "hot"` and `pre_wash_oil` is present.
- **Exceptions:** None recorded.
- **Status:** active

---

## 3. Engine rules (`ENGINE_RULES` in `backend/app/domains/routines/rules.py`)

Structural findings produced by the engine itself, not tied to an ingredient family.

### `rule.user_allergy`
- **Version:** 1.0
- **Severity:** avoid
- **Evidence source:** Not an evidence claim about the ingredient — the rule fires on the **user's own declared allergies** applied to their own inventory. Applying a user's declared instruction to their data does not need a citation; it needs correctness.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Fires only when the allergen appears in a confirmed ingredient row on an owned product.
- **Exceptions:** Low-confidence label reads generate an `unconfirmed` finding instead.
- **Status:** active

### `rule.duplicate_slot`, `rule.missing_slot`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Slot definitions live in `SKIN_SLOTS` / `HAIR_SLOTS`; these rules count what the user has against what a routine step requires. No external evidence needed for a counting rule.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Only `required=True` slots count as a gap.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.product_expired`, `rule.product_expiring`, `rule.no_expiry_recorded`
- **Version:** 1.0
- **Severity:** caution / info / info
- **Evidence source:** Cosmetic labelling: EU Cosmetics Regulation 1223/2009 for PAO (Period After Opening) symbol; and standard cosmetic-preservation guidance.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Fires from the user's own recorded dates.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.low_use_product`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Not a safety rule. Behavioural rule based on the user's own log.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Active for at least 30 days, used no more than twice, no use in the last 30 days.
- **Exceptions:** None recorded.
- **Status:** active

### `rule.unconfirmed_ingredient`
- **Version:** 1.0
- **Severity:** info
- **Evidence source:** Not an evidence claim — the rule reports a low-confidence OCR read for the user to confirm.
- **Reviewer:** @blazebrt
- **Reviewed date:** 2026-08-03
- **Applicability limits:** Any product with at least one ingredient row flagged `needs_confirmation`.
- **Exceptions:** None recorded.
- **Status:** active

---

## 4. Safety-classifier rule ids (Fix 14)

The structured safety classifier at
`backend/app/domains/routines/safety_classifier.py` carries stable
rule ids for each deterministic pattern. They cover the categories
the stabilisation brief names — diagnosis, treatment, dosage,
medication interaction, allergy certainty, pregnancy/breastfeeding,
disease claim, deficiency claim, guaranteed outcome, harmful body
judgement, unsupported causal claim, emergency symptom,
professional referral required.

Rather than reproducing the full source of the patterns here, the
document lists the rule ids in scope, and asserts that every id
declared in `safety_classifier._PATTERNS` appears in this file. A
regression test enforces the assertion.

### Diagnosis
- `safety.dx.name_condition` — evidence: general medical practice; naming a condition is out of scope for a styling app.
- `safety.dx.confirmed_condition` — same evidence class.
- `safety.dx.assert_person` — same evidence class.

### Treatment
- `safety.tx.treats` — evidence: general medical practice.
- `safety.tx.heal` — same.
- `safety.tx.cure` — same.

### Dosage
- `safety.dose.per_day` — bare-quantity dose pattern.
- `safety.dose.take_amount` — dosing verb pattern.
- `safety.dose.start_stop` — course-of-action pattern.

### Medication interaction
- `safety.rx.interacts` — interaction with prescription medicine is out of scope.
- `safety.rx.safe_with` — same.

### Allergy certainty
- `safety.allergy.definitive` — an app cannot certify an allergy.
- `safety.allergy.diagnose` — same.

### Pregnancy / breastfeeding
- `safety.preg.safe` — provider-specific advice is out of scope.
- `safety.preg.direct` — same.

### Disease claim
- `safety.disease.claim` — cosmetic products do not treat disease.
- `safety.disease.eradicate` — same.

### Deficiency claim
- `safety.deficiency.you_are` — deficiency diagnosis is out of scope.
- `safety.deficiency.condition` — same.

### Guaranteed outcome
- `safety.outcome.guarantee` — outcomes are not guaranteed.
- `safety.outcome.days` — timeline-guarantee pattern.

### Harmful body judgement
- `safety.body.score` — appearance scoring is banned product-wide.
- `safety.body.money_wasted` — banned language.
- `safety.body.problem_area` — banned language.
- `safety.body.body_type_judge` — banned language.
- `safety.body.lose_weight` — weight guidance is out of scope.

### Unsupported causal claim
- `safety.cause.dairy_acne` — evidence: Bronsnick, T. et al., "Diet and acne — a review", *J Am Acad Dermatol* 71 (2014) — the association is weak, plural-source, and does not support a causal claim in individual advice.
- `safety.cause.hormones` — same evidence class.

### Emergency symptom
- `safety.emergency.swelling_lips` — route to emergency services.
- `safety.emergency.breathing` — same.
- `safety.emergency.bleeding` — same.
- `safety.emergency.anaphylaxis` — same.

### Professional referral required
- `safety.refer.dermatologist` — informational, non-blocking.
- `safety.refer.doctor` — same.
- `safety.refer.professional` — same.

---

## 5. Ingredients we do **not** cover

The ontology (`backend/app/domains/routines/ontology.py`) enumerates
the ingredient families GlamGenius reasons about. Everything else is
**not covered** and is labelled as such in the app UI. That includes
but is not limited to:

- Prescription retinoids
- Prescription steroid creams
- Prescription antibiotic topicals
- Any oral medication
- Any topical treatment marketed as a pharmaceutical rather than a
  cosmetic

"Not covered" is not a safety statement. It is a statement about
the depth of GlamGenius's ingredient knowledge. A qualified
professional is the correct source for anything in the list above.
The `PROFESSIONAL_BOUNDARY` copy in
`backend/app/domains/routines/safety.py` renders this to the user
in plain language when the app is asked something out of scope.

---

## 6. Payment mechanics

Nothing in this document, or in the code paths it references, is a
billing or payment claim. `SUBSCRIPTIONS_AVAILABLE=false` is
unrelated to ingredient warning coverage.
