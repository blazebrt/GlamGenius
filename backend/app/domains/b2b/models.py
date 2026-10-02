"""The three Step 17 tables. Deliberately small.

Organisation and platform operational data, not consumer data: nothing here
belongs to a GlamGenius account, nothing references ``accounts``, and account
deletion never touches it. Privacy classifications (``app.domains.privacy``):

* ``b2b_api_clients`` — ``NOT_USER_OWNED``: who may call, and how often.
* ``b2b_api_keys`` — ``SECRET_EXCLUDED``: a credential hash. Never exported.
* ``b2b_api_usage_daily`` — ``OPERATIONAL``: aggregate counts per client-day.

What is absent is the point:

* no price, rate card, contract value, commission or charging field, and no
  free-form JSON configuration a field like that could hide in;
* no raw API secret — ``key_hash`` is constrained to 64 lowercase hex
  characters, so the database itself refuses a raw ``ggb_…`` key there;
* no barcode, response, grade or verdict anywhere — usage is a count of
  requests, not a record of what anybody looked up, and no Product Truth
  answer is ever stored (rules and evidence change; a stored answer would be
  a stale second authority).

Every foreign key is ``RESTRICT``: keys are revoked, clients suspended, and
neither is hard-deleted in the normal lifecycle, so history is never lost to a
cascade.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

CLIENT_STATUS_ACTIVE = "active"
CLIENT_STATUS_SUSPENDED = "suspended"
CLIENT_STATUSES: tuple[str, ...] = (CLIENT_STATUS_ACTIVE, CLIENT_STATUS_SUSPENDED)

#: A client key is an operator-chosen slug: lowercase letters, digits and inner
#: hyphens, 3–48 characters. Same pattern in the model, the migration and the
#: admin schema.
CLIENT_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$"
DISPLAY_NAME_MAX = 120

#: Bounds on what an administrator may grant. A per-minute ceiling above 600
#: is not a pilot on one free-tier web process; a daily ceiling of a million is
#: far beyond anything V1 is for.
MIN_REQUESTS_PER_MINUTE = 1
MAX_REQUESTS_PER_MINUTE = 600
MIN_REQUESTS_PER_DAY = 1
MAX_REQUESTS_PER_DAY = 1_000_000


class B2BApiClient(UUIDPrimaryKey, TimestampMixin, Base):
    """One organisation GlamGenius has chosen to give access to."""

    __tablename__ = "b2b_api_clients"

    client_key: Mapped[str] = mapped_column(String(48), nullable=False)
    #: Operator-only label. Never part of any answer.
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CLIENT_STATUS_ACTIVE, server_default=CLIENT_STATUS_ACTIVE,
    )
    requests_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    requests_per_day: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("client_key", name="uq_b2b_api_clients_client_key"),
        CheckConstraint(f"client_key ~ '{CLIENT_KEY_PATTERN}'", name="ck_b2b_api_clients_client_key"),
        CheckConstraint(
            f"length(btrim(display_name)) BETWEEN 1 AND {DISPLAY_NAME_MAX}",
            name="ck_b2b_api_clients_display_name",
        ),
        CheckConstraint("status IN ('active', 'suspended')", name="ck_b2b_api_clients_status"),
        CheckConstraint(
            f"requests_per_minute BETWEEN {MIN_REQUESTS_PER_MINUTE} AND {MAX_REQUESTS_PER_MINUTE}",
            name="ck_b2b_api_clients_requests_per_minute",
        ),
        CheckConstraint(
            f"requests_per_day BETWEEN {MIN_REQUESTS_PER_DAY} AND {MAX_REQUESTS_PER_DAY}",
            name="ck_b2b_api_clients_requests_per_day",
        ),
    )


class B2BApiKey(UUIDPrimaryKey, TimestampMixin, Base):
    """One credential issued to one client. The secret itself is never stored."""

    __tablename__ = "b2b_api_keys"

    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("b2b_api_clients.id", ondelete="RESTRICT"), nullable=False,
    )
    #: Public, random, unique: what the credential is looked up by.
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    #: SHA-256 of the whole credential, lowercase hex.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("key_prefix", name="uq_b2b_api_keys_key_prefix"),
        UniqueConstraint("key_hash", name="uq_b2b_api_keys_key_hash"),
        CheckConstraint("key_prefix ~ '^[0-9a-f]{12}$'", name="ck_b2b_api_keys_key_prefix"),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_b2b_api_keys_key_hash"),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at", name="ck_b2b_api_keys_expiry_after_issue",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at", name="ck_b2b_api_keys_revoked_after_issue",
        ),
        Index("ix_b2b_api_keys_client_created", "client_id", "created_at"),
    )


class B2BApiUsageDaily(TimestampMixin, Base):
    """How many calls one client made on one UTC day. Counts, nothing else."""

    __tablename__ = "b2b_api_usage_daily"

    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("b2b_api_clients.id", ondelete="RESTRICT"), primary_key=True,
    )
    #: The database's own UTC date, never a web process's clock.
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    #: Requests that took one unit of the daily allowance.
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Of those, answered ``available``.
    successful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Of those, answered ``not_enough_information``.
    not_enough_information_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    #: Requests refused with 429, by the burst limit or the daily allowance.
    rate_limited_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        CheckConstraint(
            "request_count >= 0 AND successful_count >= 0 "
            "AND not_enough_information_count >= 0 AND rate_limited_count >= 0",
            name="ck_b2b_api_usage_daily_non_negative",
        ),
        # An answer is only ever counted for a request that took an allowance.
        CheckConstraint(
            "successful_count + not_enough_information_count <= request_count",
            name="ck_b2b_api_usage_daily_outcomes_within_requests",
        ),
    )


__all__ = [
    "CLIENT_KEY_PATTERN",
    "CLIENT_STATUSES",
    "CLIENT_STATUS_ACTIVE",
    "CLIENT_STATUS_SUSPENDED",
    "DISPLAY_NAME_MAX",
    "MAX_REQUESTS_PER_DAY",
    "MAX_REQUESTS_PER_MINUTE",
    "MIN_REQUESTS_PER_DAY",
    "MIN_REQUESTS_PER_MINUTE",
    "B2BApiClient",
    "B2BApiKey",
    "B2BApiUsageDaily",
]
