from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.currency import CurrencySettings, CurrencyRate
from app.models.financial_record import FinancialRecord, FinancialRecordPayment


def dec(value):
    return Decimal(str(value or 0))


def get_base_currency(db: Session, owner_id: int) -> str:
    settings = db.query(CurrencySettings).filter(
        CurrencySettings.owner_id == owner_id
    ).first()
    if settings:
        return settings.base_currency

    settings = CurrencySettings(owner_id=owner_id, base_currency="RSD")
    db.add(settings)
    db.flush()
    return settings.base_currency


def _direct_rate(db, owner_id, source, target, on_date):
    row = db.query(CurrencyRate).filter(
        CurrencyRate.owner_id == owner_id,
        CurrencyRate.from_currency == source,
        CurrencyRate.to_currency == target,
        CurrencyRate.effective_date <= on_date,
    ).order_by(CurrencyRate.effective_date.desc()).first()

    if row:
        return dec(row.rate)

    row = db.query(CurrencyRate).filter(
        CurrencyRate.owner_id == owner_id,
        CurrencyRate.from_currency == target,
        CurrencyRate.to_currency == source,
        CurrencyRate.effective_date <= on_date,
    ).order_by(CurrencyRate.effective_date.desc()).first()

    if row:
        return Decimal("1") / dec(row.rate)

    return None


def exchange_rate(db, owner_id, source, target, on_date):
    source = source.upper()
    target = target.upper()

    if source == target:
        return Decimal("1")

    rate = _direct_rate(db, owner_id, source, target, on_date)
    if rate is not None:
        return rate

    base = get_base_currency(db, owner_id)
    if source == base or target == base:
        return None

    first = _direct_rate(db, owner_id, source, base, on_date)
    second = _direct_rate(db, owner_id, base, target, on_date)

    if first is None or second is None:
        return None

    return first * second


def convert_amount(db, owner_id, amount, source, target, on_date):
    rate = exchange_rate(db, owner_id, source, target, on_date)
    if rate is None:
        raise ValueError(
            f"Exchange rate not found: {source} to {target} on {on_date}"
        )
    return dec(amount) * rate, rate


def fx_report(db: Session, owner_id: int, as_of_date: date):
    base = get_base_currency(db, owner_id)

    records = db.query(FinancialRecord).filter(
        FinancialRecord.owner_id == owner_id,
        FinancialRecord.record_date <= as_of_date,
    ).order_by(FinancialRecord.record_date, FinancialRecord.id).all()

    realized = Decimal("0")
    unrealized = Decimal("0")
    missing_rates = []
    by_currency = defaultdict(lambda: {
        "realized": Decimal("0"),
        "unrealized": Decimal("0"),
        "outstanding": Decimal("0"),
    })

    for record in records:
        currency = str(record.currency or "").upper()
        total = dec(record.amount)
        sign = Decimal("1") if record.record_type == "income" else Decimal("-1")

        original_rate = exchange_rate(
            db, owner_id, currency, base, record.record_date
        )
        closing_rate = exchange_rate(
            db, owner_id, currency, base, as_of_date
        )

        if original_rate is None:
            missing_rates.append({
                "record_id": record.id,
                "currency": currency,
                "date": record.record_date.isoformat(),
                "purpose": "original_transaction",
            })
            continue

        payments = db.query(FinancialRecordPayment).filter(
            FinancialRecordPayment.record_id == record.id,
            FinancialRecordPayment.payment_date <= as_of_date,
        ).order_by(
            FinancialRecordPayment.payment_date,
            FinancialRecordPayment.id,
        ).all()

        remaining = total

        for payment in payments:
            allocated = min(max(dec(payment.amount), Decimal("0")), remaining)
            if allocated <= 0:
                continue

            payment_rate = exchange_rate(
                db, owner_id, currency, base, payment.payment_date
            )
            if payment_rate is None:
                missing_rates.append({
                    "record_id": record.id,
                    "currency": currency,
                    "date": payment.payment_date.isoformat(),
                    "purpose": "payment",
                })
            else:
                difference = allocated * (payment_rate - original_rate) * sign
                realized += difference
                by_currency[currency]["realized"] += difference

            remaining -= allocated

        if remaining > 0:
            by_currency[currency]["outstanding"] += remaining

            if closing_rate is None:
                missing_rates.append({
                    "record_id": record.id,
                    "currency": currency,
                    "date": as_of_date.isoformat(),
                    "purpose": "closing_valuation",
                })
            else:
                difference = remaining * (closing_rate - original_rate) * sign
                unrealized += difference
                by_currency[currency]["unrealized"] += difference

    return {
        "base_currency": base,
        "as_of_date": as_of_date.isoformat(),
        "realized_fx": str(realized),
        "unrealized_fx": str(unrealized),
        "total_fx": str(realized + unrealized),
        "by_currency": {
            currency: {key: str(value) for key, value in values.items()}
            for currency, values in sorted(by_currency.items())
        },
        "missing_rates": missing_rates,
    }
