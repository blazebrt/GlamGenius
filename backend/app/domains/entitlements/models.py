"""Usage ledger.

V1 tracks allowance as a single counter on the user document
(``scans_used_this_month``), which can say *how many* but never *which* or
*why*. This is an append-only ledger of every consumption event, so "you used
three checks" can be itemised — and a wrongly charged check can be found and
credited back.

The V1 counter stays authoritative for enforcement in this phase. The ledger is
written alongside it.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

FEATURE_SCAN_ANALYZE = "scan_analyze"
FEATURE_STYLE_PLAN = "style_plan"


class UsageLedgerEntry(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "usage_ledger"

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("account_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature: Mapped[str] = mapped_column(String(64), nullable=False)

    # Negative values are credits — how a wrongly consumed check is returned.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # YYYY-MM, matching V1's scan_month_key so the two can be reconciled.
    period_key: Mapped[str] = mapped_column(String(7), nullable=False)

    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_usage_ledger_account_period", "account_id", "period_key"),
        Index("ix_usage_ledger_feature", "feature"),
    )
