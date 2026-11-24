from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, JSON, Numeric, String,
    Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BankAccount(TimestampMixin, Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (
        UniqueConstraint("owner_id", "account_key", name="uq_bank_account_owner_key"),
        Index("ix_bank_accounts_owner", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(150))
    iban: Mapped[str | None] = mapped_column(String(64))
    account_number: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0")
    )
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    account_key: Mapped[str] = mapped_column(String(128), nullable=False)


class BankTransaction(TimestampMixin, Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('unmatched', 'matched', 'ignored')",
            name="bank_transaction_status",
        ),
        CheckConstraint("amount <> 0", name="bank_transaction_amount_nonzero"),
        UniqueConstraint(
            "account_id", "transaction_fingerprint",
            name="uq_bank_transaction_fingerprint",
        ),
        Index("ix_bank_transactions_account_date", "account_id", "transaction_date"),
        Index("ix_bank_transactions_status", "account_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    counterparty: Mapped[str | None] = mapped_column(String(255))
    reference: Mapped[str | None] = mapped_column(String(255))
    external_id: Mapped[str | None] = mapped_column(String(255))
    transaction_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unmatched"
    )
    matched_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_records.id", ondelete="RESTRICT")
    )
    import_batch_id: Mapped[str | None] = mapped_column(String(64))
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class BankReconciliation(TimestampMixin, Base):
    __tablename__ = "bank_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "period_start", "period_end",
            name="uq_bank_reconciliation_period",
        ),
        Index("ix_bank_reconciliations_account_period", "account_id", "period_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    statement_closing_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    calculated_closing_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    difference: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unreconciled"
    )
    note: Mapped[str | None] = mapped_column(Text)


class BankAuditEvent(Base):
    __tablename__ = "bank_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_transactions.id", ondelete="RESTRICT")
    )
    reconciliation_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_reconciliations.id", ondelete="RESTRICT")
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
