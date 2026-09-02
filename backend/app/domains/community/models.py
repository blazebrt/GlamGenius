"""One shopper's structured observation about one pack they actually scanned.

There is deliberately no text column of any kind. Not a note, not a caption,
not an "anything else?" — a free-text box is how an observation system becomes
a place where people accuse brands, and the Constitution forbids it outright.
Everything a person can say here is one code from a closed list plus a photo.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

from .observations import OBSERVATION_CODES

REPORT_STATUS_ACCEPTED = "accepted"
REPORT_STATUS_WITHDRAWN = "withdrawn"
REPORT_STATUS_UNDER_REVIEW = "under_review"
REPORT_STATUS_INVALID = "invalid"
REPORT_STATUSES = (
    REPORT_STATUS_ACCEPTED, REPORT_STATUS_WITHDRAWN,
    REPORT_STATUS_UNDER_REVIEW, REPORT_STATUS_INVALID,
)

# Closed moderation vocabulary. A moderator picks a reason; they do not write
# one, for the same reason a shopper does not.
MODERATION_WRONG_PRODUCT_CONTEXT = "wrong_product_context"
MODERATION_WRONG_BATCH_CONTEXT = "wrong_batch_context"
MODERATION_DUPLICATE_EVIDENCE = "duplicate_evidence"
MODERATION_UNSUPPORTED_MEDIA = "unsupported_media"
MODERATION_POLICY_VIOLATION = "policy_violation"
MODERATION_REASONS = (
    MODERATION_WRONG_PRODUCT_CONTEXT, MODERATION_WRONG_BATCH_CONTEXT,
    MODERATION_DUPLICATE_EVIDENCE, MODERATION_UNSUPPORTED_MEDIA,
    MODERATION_POLICY_VIOLATION,
)


def _in_list(column: str, values) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in sorted(values)) + ")"


class CommunityObservationReport(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "community_observation_reports"

    # Reporting is the one thing in this product that is not anonymous. A brand
    # can be publicly associated with a shopper's claim here, so the claim has
    # to come from a real account that scanned the pack — not a device minted
    # thirty seconds ago.
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False,
    )
    # Abuse and audit context only. Never a reporter identity: ten devices
    # claimed by one account are still one person.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scan_devices.id", ondelete="SET NULL"),
    )
    client_report_id: Mapped[str] = mapped_column(String(64), nullable=False)
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_code: Mapped[str] = mapped_column(String(48), nullable=False)

    # Nullable in the schema although required at submission: a photo may be
    # deleted later, and the audit row must survive that without blocking the
    # deletion. A row whose photo has gone stops supporting a public signal.
    photo_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"),
    )

    # The exact scan that established this report's context. This, not the
    # snapshot, is the authoritative physical-pack provenance: Step 3 excludes
    # batch_number from its semantic fingerprint, so two packets from different
    # lots legitimately share one snapshot, and only the scan event says which
    # packet was in this person's hand.
    scan_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scan_events.id", ondelete="SET NULL"),
    )
    # Present only when Step 3 allocated a snapshot for that exact scan. When it
    # deduplicated the semantic label onto an existing row, this stays null,
    # which is the honest answer.
    label_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_label_snapshots.id", ondelete="SET NULL"),
    )
    batch_number: Mapped[str | None] = mapped_column(String(160))

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=REPORT_STATUS_ACCEPTED, server_default=REPORT_STATUS_ACCEPTED,
    )
    moderation_reason: Mapped[str | None] = mapped_column(String(48))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # The final race boundary for an offline client retrying a submission.
        UniqueConstraint("account_id", "client_report_id", name="uq_community_report_idempotency"),
        CheckConstraint(_in_list("observation_code", OBSERVATION_CODES), name="ck_community_observation_code"),
        CheckConstraint(_in_list("status", REPORT_STATUSES), name="ck_community_report_status"),
        CheckConstraint(
            f"moderation_reason IS NULL OR {_in_list('moderation_reason', MODERATION_REASONS)}",
            name="ck_community_moderation_reason",
        ),
        Index("ix_community_report_product_signal", "barcode", "observation_code", "status", "created_at"),
        Index("ix_community_report_batch_signal", "barcode", "observation_code", "batch_number", "status", "created_at"),
        Index("ix_community_report_account", "account_id", "created_at"),
    )
