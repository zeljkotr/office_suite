from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Date,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Invoice(TimestampMixin, Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    invoice_number: Mapped[str | None] = mapped_column(
        String(100), index=True
    )
    direction: Mapped[str] = mapped_column(
        String(10), default="incoming", server_default="incoming", nullable=False, index=True
    )
    supplier_name: Mapped[str | None] = mapped_column(String(255))
    supplier_tax_id: Mapped[str | None] = mapped_column(String(100))
    supplier_email: Mapped[str | None] = mapped_column(String(255))

    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)

    currency: Mapped[str] = mapped_column(
        String(3), default="", nullable=False
    )

    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0, nullable=False
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0, nullable=False
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(30), default="pending_review", nullable=False, index=True
    )
    payment_status: Mapped[str] = mapped_column(
        String(30), default="unpaid", nullable=False, index=True
    )

    original_filename: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    stored_filename: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)

    ocr_text: Mapped[str | None] = mapped_column(Text)
    extraction_data: Mapped[str | None] = mapped_column(Text)
    review_notes: Mapped[str | None] = mapped_column(Text)

    duplicate_fingerprint: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )

    uploaded_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id")
    )
    paid_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id")
    )

    __table_args__ = (
        Index("ix_invoices_supplier_date", "supplier_name", "invoice_date"),
    )


class InvoiceAuditEvent(Base):
    __tablename__ = "invoice_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class InvoicePayment(Base):
    __tablename__ = "invoice_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    method: Mapped[str | None] = mapped_column(String(50))
    transaction_reference: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class InvoiceRefund(Base):
    __tablename__ = "invoice_refunds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    refunded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    method: Mapped[str | None] = mapped_column(String(50))
    transaction_reference: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    

class InvoiceCreditNote(Base):
    __tablename__ = "invoice_credit_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credit_note_number: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    is_cancellation: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class InvoiceAdvance(Base):
    __tablename__ = "invoice_advances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    party_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    method: Mapped[str | None] = mapped_column(String(50))
    transaction_reference: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )


class InvoiceAdvanceAuditEvent(Base):
    __tablename__ = "invoice_advance_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advance_id: Mapped[int] = mapped_column(
        ForeignKey("invoice_advances.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True
    )


class InvoiceAdvanceAllocation(Base):
    __tablename__ = "invoice_advance_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advance_id: Mapped[int] = mapped_column(
        ForeignKey("invoice_advances.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)


class InvoiceAdvanceRefund(Base):
    __tablename__ = "invoice_advance_refunds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advance_id: Mapped[int] = mapped_column(
        ForeignKey("invoice_advances.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    refunded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    method: Mapped[str | None] = mapped_column(String(50))
    transaction_reference: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
