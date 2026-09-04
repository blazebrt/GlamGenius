"""Resolving a printed ingredient list to canonical identities, and no further.

The whole layer answers one question:

    *What exact candidate names were printed, and which canonical identities can
    we defensibly resolve them to?*

It does not answer what any of them does, whether any of them is safe, how much
is present, whether the product is any good, or whether it suits anybody. There
is no score, grade, verdict, action, positive or negative anywhere in this
module, and there is no field one could be smuggled into.

**Step 7A is the only identity authority.** This module owns no substance table,
no synonym table, no alias map and no matching rules of its own. It parses, and
then it asks :func:`app.domains.substances.service.resolve_names`. When that
says a name is unresolved, the answer is unresolved — there is no fallback to
the legacy Care ontology, no fuzzy second attempt, no model, and no lookup on
the network. Silence is a legitimate answer and the only honest one available.

**Ambiguity survives the trip.** If Step 7A reports two entities for a printed
name, this layer reports two entities. Nothing here breaks that tie by printed
order, product category, namespace, preferred-name flag, source count, evidence
strength, the old Care families or alphabetical order. Every one of those would
be this layer inventing an answer no reviewer gave.

**A group stays a group.** Step 7A can identify a mixture or a family, and when
a reviewed name resolves to one, that is the identity reported. It is never
expanded into guessed members: a reviewed group name is not permission to decide
which exact member was in the formula.

**Duplicates survive too.** A printed list that says ``Glycerin`` twice gets two
output rows. The batch resolver deduplicates its *lookup keys* internally, which
is an efficiency detail; the formula result restores every printed occurrence,
because what the label printed is the observation being recorded.

**Position is printed order and nothing else.** Not concentration, not
importance, not dominance, not efficacy. Ordering conventions that do exist in
some regimes depend on jurisdiction, category and date, and reading one here
would be an unsourced regulatory inference — see
``docs/architecture/FORMULA_RESOLUTION.md``.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.formulas.parser import (
    FormulaParse,
    ParseStatus,
    parse_formula,
)
from app.domains.substances.service import ResolutionStatus, resolve_names


@dataclass(frozen=True)
class FormulaIngredientResolution:
    """One printed entry and the identity, if any, it defensibly denotes."""

    #: 1-based printed order. Order only — never a concentration.
    position: int
    #: Exactly as printed, minus surrounding whitespace.
    raw_name: str
    #: Step 7A's canonical lookup key, or ``None`` when the name is unusable.
    normalized_name: str | None
    status: ResolutionStatus
    #: Populated only when exactly one canonical entity was established.
    substance_key: str | None = None
    entity_kind: str | None = None
    #: Every entity the printed name could denote: one when RESOLVED, two or
    #: more when AMBIGUOUS, empty when UNRESOLVED. Deterministically ordered so
    #: a caller can name which entities were confused — never so one can be
    #: picked out of the set.
    candidate_substance_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormulaResolution:
    """Everything this layer can say about one printed ingredient list.

    Deliberately not a verdict. There is no overall score, grade, action or
    recommendation here, and adding one would make this layer start deciding
    rather than reading.
    """

    status: ParseStatus
    ingredients: tuple[FormulaIngredientResolution, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is ParseStatus.PARSED

    @property
    def resolved_count(self) -> int:
        """How many entries reached exactly one canonical identity.

        A count of what is known, not a quality measure: a formula with more
        resolved entries is better *documented*, which says nothing whatever
        about the product.
        """
        return sum(1 for row in self.ingredients if row.status is ResolutionStatus.RESOLVED)


async def resolve_formula(session: AsyncSession, ingredients_text: object) -> FormulaResolution:
    """Parse a printed ingredient list and resolve each entry against Step 7A.

    One batch call to the identity resolver for the entire formula, whatever its
    length — never one call per ingredient. A formula has dozens of entries, so
    a per-entry loop would be an N+1 the moment it shipped; the parser's token
    ceiling is set equal to the resolver's batch ceiling precisely so a parsed
    formula always fits one call.

    Any parse failure returns that status with no ingredients at all, and no
    query is issued: there is nothing to look up, and half a formula is worse
    than none.
    """
    parse: FormulaParse = parse_formula(ingredients_text)
    if not parse.ok:
        return FormulaResolution(status=parse.status, ingredients=())
    if not parse.tokens:
        # Defensive: PARSED always carries at least one token today, and a
        # future change that broke that must not reach the resolver with an
        # empty batch and report a confidently empty formula.
        return FormulaResolution(status=ParseStatus.EMPTY, ingredients=())

    # One call, for the whole formula. The resolver returns one answer per
    # input, in input order, including for duplicates — so zipping positions
    # back onto it restores every printed occurrence.
    resolutions = await resolve_names(session, [token.raw_name for token in parse.tokens])

    ingredients = tuple(
        FormulaIngredientResolution(
            position=token.position,
            raw_name=token.raw_name,
            normalized_name=resolution.normalized_name,
            status=resolution.status,
            substance_key=resolution.substance_key,
            entity_kind=resolution.entity_kind,
            candidate_substance_keys=resolution.candidate_substance_keys,
        )
        for token, resolution in zip(parse.tokens, resolutions, strict=True)
    )
    return FormulaResolution(status=ParseStatus.PARSED, ingredients=ingredients)


__all__ = [
    "FormulaIngredientResolution",
    "FormulaResolution",
    "resolve_formula",
]
