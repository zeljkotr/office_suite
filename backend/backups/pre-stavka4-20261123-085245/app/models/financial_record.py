from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, JSON,
    Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FinancialRecord(TimestampMixin, Base):
    __tablename__ = "financial_records"
    __table_args__ = (
        CheckConstraint("record_type IN ('income', 'expense')", name="record_type"),
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint(
            "owner_id", "record_type", "duplicate_fingerprint",
            name="uq_financial_record_duplicate",
        ),
        Index("ix_financial_records_owner_date", "owner_id", "record_date"),
        Index("ix_financial_records_type_date", "record_type", "record_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_type: Mapped[str] = mapped_column(String(10), nullable=False)
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    counterparty: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str | None] = mapped_column(String(60))
    document_number: Mapped[str | None] = mapped_column(String(100))
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT")
    )
    attachments: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    duplicate_fingerprint: Mapped[str | None] = mapped_column(String(64))
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class FinancialRecordPayment(TimestampMixin, Base):
    __tablename__ = "financial_record_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="payment_amount_positive"),
        Index("ix_financial_record_payments_record", "record_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("financial_records.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String(60))
    reference: Mapped[str | None] = mapped_column(String(150))
    notes: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class FinancialRecordAuditEvent(Base):
    __tablename__ = "financial_record_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("financial_records.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
