from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.financial_record import FinancialRecordPayment


def payment_totals(db: Session, record_id: int) -> Decimal:
    value = db.query(
        func.coalesce(func.sum(FinancialRecordPayment.amount), 0)
    ).filter(FinancialRecordPayment.record_id == record_id).scalar()
    return Decimal(str(value or 0))


def financial_status(total, paid):
    total = Decimal(str(total))
    paid = Decimal(str(paid))
    if paid <= 0:
        return "unpaid"
    if paid >= total:
        return "paid"
    return "partially_paid"
