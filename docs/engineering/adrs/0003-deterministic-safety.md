# ADR 0003 — Deterministic safety layer + additive model second-opinion

**Status:** Accepted. **Date:** 2026-08-03. **Deciders:** @blazebrt.

## Context

A styling app that talks about the body has to hold specific policy
lines: no diagnosis, no dosage, no medical claim, no appearance
score. Relying only on a banned-word sweep is brittle; relying only
on a model to judge itself is unreliable.

## Decision

Two layers in this order.

1. **Deterministic classifier** (`safety_classifier.py`, Fix 14).
   Every rule is a reviewed regex with a stable id documented in
   `INGREDIENT_COVERAGE.md`. `is_blocked_for_display(text)` fails
   closed on any blocking category.
2. **Model second-opinion** (optional). If added, it runs the same
   text and proposes ADDITIONAL categories. The union is what
   applies; the model can never remove a deterministic finding.
3. **Banned-word sweep** in `safety.py` remains as the secondary
   defence.

## Consequences

- The safety floor is code, not a prompt.
- A model getting cleverer can only make the result more
  restrictive, never less.
- Adding a new safety category is a code change with a reviewed
  regex — auditable, testable, revertable.

## Alternatives considered

- **Model-only**: rejected. Cannot be tested for regression.
- **Word-list-only**: rejected. Every synonym is a defect.
- **Deterministic-only**: acceptable, but a model second-opinion is
  a cheap way to catch what the reviewed rules missed as long as
  it cannot argue us out of a floor.

## Related

- `backend/app/domains/routines/safety.py`
- `backend/app/domains/routines/safety_classifier.py`
- `docs/stabilisation/INGREDIENT_COVERAGE.md`
