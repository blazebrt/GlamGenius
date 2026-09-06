"""The release table — Store B, global, account-independent.

One row is one reviewed decision bundle: its manifest, the hash that proves
the manifest has not changed since review, where it sits in its lifecycle, and
who moved it there.

There are deliberately no child tables for semantic, policy or explanation
rules. The unit that is reviewed, approved and activated is the whole bundle,
and rows a reviewer can edit one at a time would make the reviewed thing and
the activated thing two different objects. The manifest is stored whole, and
the hash makes editing it outside the reviewed path detectable rather than
merely discouraged.

There is no ``account_id`` and no ``device_id``. A release is governed global
knowledge that is identical for everybody; who it applies to is decided at
runtime by Step 8B against that person's own trusted facts. Personalising the
release itself would move that decision into a place nobody reviews per
person. Nothing in the manifest may carry customer data — see
``validation.assert_manifest_carries_no_personal_data``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.personal_decision_release.enums import PersonalDecisionReleaseStatus
from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

_STATUSES = tuple(status.value for status in PersonalDecisionReleaseStatus)

#: The partial unique index that makes "at most one active release" a database
#: fact rather than an application convention. Application code checks it too,
#: but two admins pressing activate at the same moment is exactly the case
#: where application checks race and the index does not.
ACTIVE_RELEASE_INDEX = "uq_personal_decision_releases_active"


class PersonalDecisionRelease(UUIDPrimaryKey, TimestampMixin, Base):
    """One immutable reviewed bundle of governed personal decision knowledge."""

    __tablename__ = "personal_decision_releases"

    #: The production series. Constant in V1 and never taken from a request:
    #: a client-supplied key would let an unreviewed series be created and
    #: activated beside the reviewed one.
    release_key: Mapped[str] = mapped_column(String(120), nullable=False)

    #: Sequential within the series, starting at 1. Ordering only -- version 7
    #: is not preferred to version 6 anywhere. Activation status alone decides
    #: which release production uses.
    release_version: Mapped[int] = mapped_column(Integer, nullable=False)

    manifest_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    #: SHA-256 over the canonical manifest, lowercase hex. Recomputed on every
    #: read; a mismatch means the row was edited outside the reviewed path.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PersonalDecisionReleaseStatus.DRAFT.value,
        server_default=PersonalDecisionReleaseStatus.DRAFT.value,
    )

    #: The named human attestations that review actually happened. Null on a
    #: fresh or edited draft: an edit clears it, because a review of the
    #: previous rules says nothing about the new ones.
    review_verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_by: Mapped[str] = mapped_column(String(160), nullable=False)

    approved_by: Mapped[str | None] = mapped_column(String(160))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    activated_by: Mapped[str | None] = mapped_column(String(160))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    retired_by: Mapped[str | None] = mapped_column(String(160))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Audit lineage only. Never consulted to choose a release, and never a
    #: route back: retiring a release does not reactivate what it superseded.
    supersedes_release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("personal_decision_releases.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        UniqueConstraint("release_key", "release_version", name="uq_personal_decision_release_version"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{value}'" for value in _STATUSES) + ")",
            name="ck_personal_decision_releases_status",
        ),
        CheckConstraint("release_version > 0", name="ck_personal_decision_releases_version"),
        CheckConstraint("btrim(release_key) <> ''", name="ck_personal_decision_releases_key"),
        CheckConstraint("btrim(created_by) <> ''", name="ck_personal_decision_releases_created_by"),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_personal_decision_releases_hash_shape"
        ),
        Index(
            ACTIVE_RELEASE_INDEX,
            "release_key",
            unique=True,
            postgresql_where=text(f"status = '{PersonalDecisionReleaseStatus.ACTIVE.value}'"),
        ),
    )
