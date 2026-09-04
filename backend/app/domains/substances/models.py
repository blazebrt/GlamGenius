"""Canonical substance identity — Store B, global, account-independent.

Two tables, and the split between them is the whole design.

``substances`` is an **entity**: a stable internal key for one thing that a
reviewed source names. It carries no property of that thing. There is
deliberately no ``family``, ``function``, ``benefit``, ``risk``, ``risk_tier``,
``safety``, ``efficacy``, ``regulatory_status``, ``concentration``, ``dose``,
``absorption``, ``interaction``, ``verdict`` or ``grade`` column. Each of those
is a claim about the substance *in a context*, needs its own evidence, and
belongs to a later milestone. A column here would be an unsourced global
assertion, which is precisely what the Constitution forbids.

``substance_names`` is a **materialised index over evidence**, not independently
authored knowledge. Every row exists because an ``EvidenceClaim`` said so, and
points back at that claim. It carries no review state of its own — no
``review_status``, no ``verified``, no ``approved`` — because the claim already
owns that state and a second copy would drift. A row may exist while its claim
is still a draft; it is simply inert until the claim clears the full public
publication boundary.

Neither table has an ``account_id`` or a ``device_id``. Identity is a fact about
the world, identical for everybody, and personalising it would make the same
name mean different things to different people.

Both tables are Store B. Nothing here goes near Store A — see
``docs/architecture/ODBL_DATA_WALL.md``.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.substances.enums import ENTITY_KINDS, NAME_NAMESPACES, SUBSTANCE_STATUSES
from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


def _check(name: str, column: str, values: tuple[str, ...]) -> CheckConstraint:
    literal = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({literal})", name=name)


class Substance(UUIDPrimaryKey, TimestampMixin, Base):
    """One identified substance or material. Identity only."""

    __tablename__ = "substances"

    #: The stable internal identifier, chosen explicitly by a human author and
    #: never derived from a display name. Deriving it from a name would tie the
    #: entity's identity to one particular spelling, so renaming or correcting a
    #: name would silently become a different entity. Lowercase, bounded, and
    #: treated as immutable once created: the service refuses to change it.
    substance_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    entity_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active",
    )

    __table_args__ = (
        _check("ck_substances_entity_kind", "entity_kind", ENTITY_KINDS),
        _check("ck_substances_status", "status", SUBSTANCE_STATUSES),
        # A key is a machine identifier: lowercase, no whitespace, bounded
        # alphabet. Enforced in the database as well as the service so a direct
        # insert cannot introduce a key the resolver would never match.
        CheckConstraint(
            "substance_key ~ '^[a-z0-9][a-z0-9_.-]*$'",
            name="ck_substances_key_shape",
        ),
    )


class SubstanceName(UUIDPrimaryKey, TimestampMixin, Base):
    """One name an evidence claim records for one substance.

    **``normalized_name`` is deliberately not unique.** The same exact text can
    genuinely name two different things, and a unique index would force the
    database to pick a winner — silently, by insertion order, at write time,
    with no reviewer involved. Ambiguity is a real answer here: the resolver
    reports AMBIGUOUS and refuses to choose. The only uniqueness enforced is
    against duplicate rows of the *same* claim/substance/name/namespace, which
    is bookkeeping rather than a judgement.
    """

    __tablename__ = "substance_names"

    substance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("substances.id", ondelete="CASCADE"), nullable=False,
    )
    #: The claim that records this name. RESTRICT, not CASCADE: an identity row
    #: must never outlive the evidence that justifies it, and deleting the
    #: evidence out from under it should fail loudly rather than orphan a name.
    identity_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_claims.id", ondelete="RESTRICT"), nullable=False,
    )
    #: The name exactly as the source printed it, for display and for audit.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: The server-computed lookup key. Never accepted from a caller.
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    namespace: Mapped[str] = mapped_column(String(32), nullable=False)
    #: BCP-47-ish tag, or ``und`` when the source does not say. Kept because a
    #: name is only unambiguous inside a language.
    language_tag: Mapped[str | None] = mapped_column(String(32))
    is_preferred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )

    __table_args__ = (
        _check("ck_substance_names_namespace", "namespace", NAME_NAMESPACES),
        CheckConstraint("btrim(name) <> ''", name="ck_substance_names_name_present"),
        CheckConstraint(
            "btrim(normalized_name) <> ''", name="ck_substance_names_normalized_present",
        ),
        # Bookkeeping only: the same claim may not record the same name for the
        # same substance twice. It says nothing about two *different* claims
        # recording the same text for different substances — that is ambiguity,
        # and it is allowed to exist.
        UniqueConstraint(
            "identity_claim_id", "substance_id", "normalized_name", "namespace",
            name="uq_substance_name_claim_identity",
        ),
        # The resolver's entry point: exact equality on the normalized key.
        Index("ix_substance_names_normalized", "normalized_name"),
        Index("ix_substance_names_substance", "substance_id"),
        Index("ix_substance_names_claim", "identity_claim_id"),
    )


__all__ = ["Substance", "SubstanceName"]
