from datetime import date, datetime, timezone

from sqlalchemy import (
    Date, DateTime, ForeignKey, Index, JSON, String, Text,
    event, select,
)
from sqlalchemy.orm import Mapped, mapped_column, Session

from app.models.base import Base


class AccountingPeriod(Base):
    __tablename__ = "accounting_periods"
    __table_args__ = (
        Index("ix_accounting_periods_owner_dates", "owner_id", "start_date", "end_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False, default="custom")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountingPeriodAudit(Base):
    __tablename__ = "accounting_period_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_periods.id", ondelete="RESTRICT"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ClosedAccountingPeriodError(Exception):
    """Raised when a financial record is changed inside a closed period."""


_DATE_FIELDS = (
    "record_date", "payment_date", "transaction_date", "value_date",
    "invoice_date", "paid_at", "refunded_at", "issued_at",
    "received_at", "allocated_at", "tax_date", "effective_date",
)


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _related_owner(session, table_name, record_id):
    table = Base.metadata.tables.get(table_name)
    if table is None or record_id is None or "id" not in table.c:
        return None
    owner_field = "uploaded_by" if table_name == "invoices" else "created_by"
    if owner_field not in table.c:
        return None
    return session.execute(
        select(table.c[owner_field]).where(table.c.id == record_id)
    ).scalar_one_or_none()


def _owner_id(session, obj):
    owner = getattr(obj, "owner_id", None)
    if owner is not None:
        return owner

    owner = getattr(obj, "uploaded_by", None)
    if owner is not None:
        return owner

    invoice_id = getattr(obj, "invoice_id", None)
    if invoice_id is not None:
        owner = _related_owner(session, "invoices", invoice_id)
        if owner is not None:
            return owner

    advance_id = getattr(obj, "advance_id", None)
    if advance_id is not None:
        owner = _related_owner(session, "invoice_advances", advance_id)
        if owner is not None:
            return owner

    return getattr(obj, "created_by", None)


def _check_object_period(session, obj):
    if isinstance(obj, (AccountingPeriod, AccountingPeriodAudit)):
        return

    owner_id = _owner_id(session, obj)
    if owner_id is None:
        return

    for field in _DATE_FIELDS:
        value = _as_date(getattr(obj, field, None))
        if value is None:
            continue

        period = session.execute(
            select(AccountingPeriod).where(
                AccountingPeriod.owner_id == owner_id,
                AccountingPeriod.status == "closed",
                AccountingPeriod.start_date <= value,
                AccountingPeriod.end_date >= value,
            ).limit(1)
        ).scalar_one_or_none()

        if period is not None:
            raise ClosedAccountingPeriodError(
                f"Accounting period '{period.name}' is closed "
                f"({period.start_date} to {period.end_date}). "
                "Changes to records in this period are not allowed."
            )

@event.listens_for(Session, "before_flush")
def prevent_closed_period_changes(session, flush_context, instances):
    for obj in session.new:
        _check_object_period(session, obj)

    for obj in session.dirty:
        if session.is_modified(obj, include_collections=False):
            _check_object_period(session, obj)

    for obj in session.deleted:
        _check_object_period(session, obj)
