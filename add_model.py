content = open('app/domains/product/models.py', 'r').read()

new_model = '''
class ScanDecisionEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """Immutable account-owned record of a purchase decision made directly from a product scan."""

    __tablename__ = "scan_decision_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    label_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product_label_snapshots.id", ondelete="SET NULL"))
    label_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        Index("ix_scan_decision_events_account_barcode_created", "account_id", "barcode", "created_at"),
    )
'''
if "class ScanDecisionEvent" not in content:
    content += new_model
    open('app/domains/product/models.py', 'w').write(content)
    print("Added ScanDecisionEvent")
