"""Product Truth: the canonical grade of one product, and the only path to it.

Every reader that publishes a food grade gets it from here: the consumer
Product Result (``GET /api/v2/scan/verdict/{barcode}``) and, since Step 17, the
B2B Product Truth API (``GET /api/b2b/v1/products/{barcode}/truth``). There is
one sequence and it lives in one place:

1. the deterministic gate engine grades the product
   (:func:`app.domains.nutrition.grading.grade_product`);
2. the publication boundary withholds a letter whenever a required rule has
   not finished the evidence lifecycle
   (:func:`app.domains.nutrition.grading.production_rules.enforce_published_required_rules`);
3. the presentation boundary turns the result into keys, bands, factors and
   their evidence (:func:`app.domains.nutrition.grading.presentation.present`).

A second copy of those three lines is how a second reader ends up skipping the
publication boundary and selling a candidate constant as a grade. So no reader
calls them itself.

What this module deliberately does **not** take
------------------------------------------------
No device, no account, no household subject, no client, no request. Product
Truth is a fact about a product and the published rules, not about who is
asking. Everything that *is* about the asker — whether this caller is holding
the pack, the official record that matches their lot, their shelf, their
memory, their personal lens — is layered on afterwards by the consumer route
and never enters here. That is why the B2B API can call this without inventing
a scanning device it does not have.

The ruleset is a parameter rather than something resolved here, so the caller
names the evidence state it is grading under and the same product graded under
the same ruleset is the same answer, every time, for every reader.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.nutrition.grading import GradeResult, ProductInput, grade_product, presentation
from app.domains.nutrition.grading.production_rules import (
    ProductionRuleset,
    enforce_published_required_rules,
)


@dataclass(frozen=True)
class GradedProduct:
    """One product graded under one ruleset, and what the screen shows of it."""

    product: ProductInput
    result: GradeResult
    ruleset: ProductionRuleset
    #: ``presentation.present`` output, unmodified. Callers that add their own
    #: envelopes add them to their own copy of the response, never back here.
    payload: dict[str, Any]


def grade(product: ProductInput, ruleset: ProductionRuleset) -> GradedProduct:
    """Grade ``product`` under ``ruleset``. Pure: no I/O, no clock, no caller."""
    result = enforce_published_required_rules(grade_product(product), ruleset)
    payload = presentation.present(product, result, ruleset)
    return GradedProduct(product=product, result=result, ruleset=ruleset, payload=payload)


__all__ = ["GradedProduct", "grade"]
