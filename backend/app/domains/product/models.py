"""Store B's record of a scanned product.

Deliberately holds no Open Food Facts field. Product name, brand, ingredients
and nutrition all live in Store A and are paired in at query time — copying
them here would build the derived database ODbL's share-alike clause acts on.
``test_odbl_data_wall.py`` and ``test_product_scan.py`` both check that.

What is here is ours: the barcode as a shared key, how far the record is
trusted, the FSSAI licence read off the pack, and who confirmed what.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.product.confidence import CONFIDENCE_LEVELS, ProductConfidence
from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

_CONFIDENCE_IN = ", ".join(f"'{level}'" for level in CONFIDENCE_LEVELS)


class ScanDevice(UUIDPrimaryKey, TimestampMixin, Base):
    """A phone that has scanned something, with no account behind it.

    The camera has to open on first launch with nothing set up, so a device
    identifies itself rather than a person. That keeps lookups attributable and
    rate-limitable without anybody signing up, and without opening a public
    endpoint. ``claimed_by_account_id`` is how a device's scans follow someone
    who later creates an account.
    """

    __tablename__ = "scan_devices"

    device_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(24))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claimed_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        Index("ix_scan_devices_claimed", "claimed_by_account_id"),
    )


class ProductRecord(UUIDPrimaryKey, TimestampMixin, Base):
    """What we know about one barcode, and how far it can be trusted."""

    __tablename__ = "product_records"

    barcode: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Never unset. A record with nothing behind it says so.
    confidence: Mapped[str] = mapped_column(
        String(32), nullable=False,
        default=ProductConfidence.NOT_ENOUGH_INFORMATION.value,
        server_default=ProductConfidence.NOT_ENOUGH_INFORMATION.value,
    )
    # A fact about the pack, not a judgement about the food.
    fssai_licence: Mapped[str | None] = mapped_column(String(20))
    # Where the record came from: off | label_capture | team | community
    origin: Mapped[str] = mapped_column(String(24), nullable=False, default="off", server_default="off")
    #: Independent confirmations. Enough of them promotes it to community level.
    confirmation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(f"confidence IN ({_CONFIDENCE_IN})", name="ck_product_records_confidence"),
        CheckConstraint("confirmation_count >= 0", name="ck_product_records_confirmations"),
        Index("ix_product_records_confidence", "confidence"),
    )


class LabelErrorReport(UUIDPrimaryKey, TimestampMixin, Base):
    """Somebody told us a number on a pack is wrong.

    Its own table rather than a scan with a note attached: a complaint is not a
    scan, and filing it as one would put it in the person's scan history and
    count it towards how much they have scanned.

    ``client_report_id`` makes the offline queue safe to replay, the same way
    ``client_scan_id`` does for scans.
    """

    __tablename__ = "label_error_reports"

    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scan_devices.id", ondelete="CASCADE"),
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
    )
    client_report_id: Mapped[str] = mapped_column(String(64), nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(64))
    #: What was on screen when they tapped: a number, an ingredient, the grade.
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    #: wrong_number | wrong_ingredient | wrong_product | wrong_grade |
    #: pack_changed | something_else
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Where the photo of the pack went, when one was attached.
    photo_key: Mapped[str | None] = mapped_column(String(200))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("device_id", "client_report_id", name="uq_label_report_device_client_id"),
        Index("ix_label_error_reports_barcode", "barcode", "created_at"),
        Index("ix_label_error_reports_open", "resolved_at"),
    )


class CommunityObservationReport(UUIDPrimaryKey, TimestampMixin, Base):
    """One structured pack observation, never a review or product conclusion.

    The policy module consumes normalized aggregates from these rows.  Nothing
    on this model can update canonical product facts, scientific scoring, or an
    official record.
    """

    __tablename__ = "community_observation_reports"

    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scan_devices.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE")
    )
    client_report_id: Mapped[str] = mapped_column(String(64), nullable=False)
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_code: Mapped[str] = mapped_column(String(80), nullable=False)
    batch_number: Mapped[str | None] = mapped_column(String(80))
    photo_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    condition_context: Mapped[dict | None] = mapped_column(JSONB)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # accepted | rejected | under_review. Only accepted rows are aggregated.
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="accepted", server_default="accepted"
    )
    # valid | invalid. Kept separately from moderation so invalid records can
    # remain auditable without becoming policy evidence.
    validity_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="valid", server_default="valid"
    )

    __table_args__ = (
        CheckConstraint("status IN ('accepted', 'rejected', 'under_review')", name="ck_community_report_status"),
        CheckConstraint("validity_state IN ('valid', 'invalid')", name="ck_community_report_validity"),
        UniqueConstraint("device_id", "client_report_id", name="uq_community_report_device_client_id"),
        Index("ix_community_report_barcode_code_created", "barcode", "observation_code", "created_at"),
        Index("ix_community_report_batch_aggregate", "barcode", "observation_code", "batch_number", "created_at"),
        Index("ix_community_report_validity_created", "status", "validity_state", "created_at"),
        Index("ix_community_report_account", "account_id", "created_at"),
    )


class FssaiComplaintHandoff(UUIDPrimaryKey, TimestampMixin, Base):
    """A structured, user-confirmed preparation for the official portal.

    This is not a complaint filing and never represents that FSSAI accepted a
    complaint. The person reviews the pack facts here, then completes the
    submission in FSSAI's own authenticated system.
    """

    __tablename__ = "fssai_complaint_handoffs"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    # food_safety | label_information | misleading_claim | packaging
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand: Mapped[str] = mapped_column(String(160), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(80), nullable=False)
    fssai_licence: Mapped[str] = mapped_column(String(20), nullable=False)
    photo_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False
    )
    # reviewed | official_portal_opened. Neither means "filed".
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="reviewed", server_default="reviewed"
    )
    official_portal_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        CheckConstraint(
            "reason IN ('food_safety', 'label_information', 'misleading_claim', 'packaging')",
            name="ck_fssai_handoff_reason",
        ),
        CheckConstraint(
            "status IN ('reviewed', 'official_portal_opened')",
            name="ck_fssai_handoff_status",
        ),
        Index("ix_fssai_handoff_status_created", "status", "created_at"),
    )


class ScanEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """One scan, recorded once.

    ``client_scan_id`` is what makes the offline queue safe: a phone that loses
    its connection mid-sync replays the queue, and the same scan arriving twice
    is recognised rather than counted twice.
    """

    __tablename__ = "scan_events"

    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scan_devices.id", ondelete="CASCADE"),
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
    )
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    #: found_local | found_off | not_found | label_captured
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    client_scan_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set when the phone had been offline and is catching up.
    queued_offline: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    label_facts: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("device_id", "client_scan_id", name="uq_scan_event_device_client_id"),
        Index("ix_scan_events_barcode", "barcode"),
    )


class LabelSnapshot(UUIDPrimaryKey, TimestampMixin, Base):
    """Versioned Store-B facts read from and confirmed against a physical pack."""

    __tablename__ = "product_label_snapshots"

    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scan_devices.id", ondelete="SET NULL"))
    scan_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scan_events.id", ondelete="RESTRICT"), nullable=False, unique=True)
    facts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default=ProductConfidence.UNVERIFIED.value)

    __table_args__ = (
        CheckConstraint(f"confidence IN ({_CONFIDENCE_IN})", name="ck_product_label_snapshots_confidence"),
        Index("ix_product_label_snapshots_barcode_created", "barcode", "created_at"),
    )
