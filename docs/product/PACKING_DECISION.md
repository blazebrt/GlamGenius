# Packing decision (Fix 7, WP4)

## Status

**Planned, not implemented.** The feature exists as a catalogue
constant (`FEATURE_PACKING` in `backend/app/domains/billing/catalogue.py`)
and appears in one narrative string (`backend/app/domains/progress/registry.py`),
but there is no end-to-end user-facing packing feature today. This
document records the decision, the scope, and the acceptance
criteria for when it is built — so the product truthfully labels
what it is.

## Decision

For the non-payment stabilisation phase, GlamGenius will **not**
ship packing-and-occasion-prep as an active user-facing feature.
The billing catalogue's `FEATURE_PACKING` entry is retained so
Work Package 7 (billing, later) can point at a stable feature id
without a rewrite, but no runtime code path fulfils it and no
frontend surface offers it. The word "packing" is removed from
any user-facing copy string that promises it as a shipping
capability during this phase.

## Why

Two reasons.

1. **Truth.** A billing catalogue entry with no fulfilment is a
   trust defect. Users must not be sold access to a feature that
   silently returns nothing.
2. **Scope.** Packing is a distinct product area (trip length,
   climate at destination, laundry rhythm, occasion-set assembly).
   It sits outside the styling/skin/hair core the beta is testing.
   Making it real is a Work Package of its own, after the beta has
   validated the core loop.

## Acceptance criteria (for when this fix lands as an active feature)

- A `POST /api/v2/packing/plan` endpoint that reads a user's
  inventory, destination climate, trip length, and declared
  occasion mix, and returns a **suggested** packing list that is
  clearly labelled as a suggestion, not a rule.
- Reads only owned inventory; never suggests buying something.
- A frontend screen with a checklist the user edits — the app
  never claims a "correct" list.
- The `FEATURE_PACKING` entitlement in the billing catalogue is
  activated, with an explicit product decision (paid or free).
- The narrative-safety layer (Fix 14) sweeps every generated
  string.
- The mobile-UX checklist (`docs/engineering/CHECKLIST_MOBILE_UX.md`)
  is walked for the screen.
- The evidence checklist (`docs/engineering/CHECKLIST_EVIDENCE.md`)
  is completed on the PR that ships this feature.

## Owner action for this branch

None. Fix 7 is a documented-planned status. No code change on this
branch enables a packing endpoint or a packing UI.

## Payment mechanics

Nothing here modifies payment mechanics. `SUBSCRIPTIONS_AVAILABLE=false`
remains. The `FEATURE_PACKING` catalogue id is untouched because
the billing surface is out of scope for the non-payment stabilisation
phase.
