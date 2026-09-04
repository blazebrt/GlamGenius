"""Controlled vocabularies for canonical substance identity.

Deliberately tiny. Every value here answers *what a thing is*, never whether it
is good, safe, effective, permitted, or present at some concentration. Adding a
member that expresses a judgement is the mistake this module exists to prevent —
see ``docs/architecture/SUBSTANCE_IDENTITY.md``.
"""
from enum import StrEnum


class EntityKind(StrEnum):
    """What kind of thing an identity row names.

    The distinction that matters most is between a *defined substance* and the
    looser things a label may also print. Collapsing them is how "contains a
    ceramide" quietly becomes "is Ceramide NP".
    """

    #: One chemically defined substance — a specific molecule, salt or ester.
    DEFINED_SUBSTANCE = "defined_substance"
    #: Material of botanical origin, named as the material rather than a molecule.
    BOTANICAL_MATERIAL = "botanical_material"
    #: A deliberate combination supplied and named as one thing.
    MIXTURE = "mixture"
    #: A named family or class. A group is NOT any of its members, and a member
    #: is not the group: that equivalence is exactly what Step 7A refuses.
    GROUP = "group"


class SubstanceStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class NameNamespace(StrEnum):
    """Which naming system a name belongs to.

    Namespace is part of identity, not decoration: the same text can be a valid
    INCI name and an unrelated common word, and a resolver that forgets which
    system it is reading has already lost the distinction.

    There is deliberately no ``marketing`` namespace. A marketing term that
    names a blend or a family is its own mixture/group entity, never an alias
    pointing at a different molecule.
    """

    #: International Nomenclature of Cosmetic Ingredients.
    INCI = "inci"
    #: A systematic or binomial scientific name.
    SCIENTIFIC = "scientific"
    #: An ordinary-usage name that a reviewed source records for this entity.
    COMMON = "common"
    #: A name as printed in an official register or reference work.
    OFFICIAL_REFERENCE = "official_reference"
    #: Anything reviewed that fits none of the above. Never a catch-all for
    #: "we did not check".
    OTHER = "other"


ENTITY_KINDS: tuple[str, ...] = tuple(x.value for x in EntityKind)
SUBSTANCE_STATUSES: tuple[str, ...] = tuple(x.value for x in SubstanceStatus)
NAME_NAMESPACES: tuple[str, ...] = tuple(x.value for x in NameNamespace)

__all__ = [
    "ENTITY_KINDS",
    "NAME_NAMESPACES",
    "SUBSTANCE_STATUSES",
    "EntityKind",
    "NameNamespace",
    "SubstanceStatus",
]
