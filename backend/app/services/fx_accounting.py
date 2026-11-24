from datetime import date, datetime
from decimal import Decimal

from app.models.currency import CurrencySettings
from app.models.invoice import (
    Invoice,
    InvoicePayment,
    InvoiceRefund,
    InvoiceCreditNote,
    InvoiceAdvanceAllocation,
)
from app.models.bank_reconciliation import BankTransaction
from app.services.currency import exchange_rate


ZERO = Decimal("0")


def dec(value):
    return Decimal(str(value or 0))


def fx_difference(amount, original_rate, settlement_rate, direction=1):
    """Difference between carrying value and settlement value in base currency."""
    return (
        dec(amount)
        * (dec(settlement_rate) - dec(original_rate))
        * Decimal(str(direction))
    )


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


def _rate(db, owner_id, source, target, on_date):
    if source == target:
        return Decimal("1")
    if not on_date:
        return None
    try:
        value = exchange_rate(db, owner_id, source, target, on_date)
        return dec(value) if value is not None else None
    except (ValueError, LookupError):
        return None


def _sum_for(db, model, invoice_id):
    value = (
        db.query(model)
        .filter(model.invoice_id == invoice_id)
    )
    return value


def accounting_fx_report(db, owner_id, as_of=None):
    as_of = as_of or date.today()

    settings = (
        db.query(CurrencySettings)
        .filter(CurrencySettings.owner_id == owner_id)
        .first()
    )
    base = (settings.base_currency if settings else "RSD").upper()

    result = {
        "as_of": as_of.isoformat(),
        "base_currency": base,
        "invoices": {
            "invoice_count": 0,
            "realized_fx": ZERO,
            "unrealized_fx": ZERO,
            "outstanding_base": ZERO,
            "by_currency": {},
            "details": [],
        },
        "bank_transactions": {
            "transaction_count": 0,
            "converted_total": ZERO,
            "by_currency": {},
            "details": [],
        },
        "missing_rates": [],
        "notes": [
            "Invoice and bank transaction totals are reported separately.",
            "Bank transaction conversions are not added to invoice settlements.",
            "Credit notes reduce outstanding exposure; they are not cash settlements.",
            "Advance allocations reduce outstanding exposure. Their historical FX is not "
            "treated as realized without a separately identifiable advance carrying value.",
        ],
    }

    invoices = (
        db.query(Invoice)
        .filter(Invoice.uploaded_by == owner_id)
        .filter(Invoice.status.in_(["approved", "cancelled"]))
        .order_by(Invoice.invoice_date, Invoice.id)
        .all()
    )

    for invoice in invoices:
        currency = (invoice.currency or base).upper()
        invoice_date = _date(invoice.invoice_date)
        original_rate = _rate(db, owner_id, currency, base, invoice_date)

        payments = _sum_for(db, InvoicePayment, invoice.id).all()
        refunds = _sum_for(db, InvoiceRefund, invoice.id).all()
        credits = _sum_for(db, InvoiceCreditNote, invoice.id).all()
        allocations = (
            db.query(InvoiceAdvanceAllocation)
            .filter(InvoiceAdvanceAllocation.invoice_id == invoice.id)
            .all()
        )

        gross = dec(invoice.total_amount)
        credited = sum((dec(item.amount) for item in credits), ZERO)
        paid = sum((dec(item.amount) for item in payments), ZERO)
        refunded = sum((dec(item.amount) for item in refunds), ZERO)
        advanced = sum((dec(item.amount) for item in allocations), ZERO)
        outstanding = max(ZERO, gross - credited - paid + refunded - advanced)

        direction = 1 if invoice.direction == "outgoing" else -1
        realized = ZERO
        missing = []

        if original_rate is None:
            missing.append({
                "type": "invoice",
                "invoice_id": invoice.id,
                "currency": currency,
                "date": invoice_date.isoformat() if invoice_date else None,
            })
        else:
            for payment in payments:
                paid_date = _date(payment.paid_at)
                settlement_rate = _rate(db, owner_id, currency, base, paid_date)
                if settlement_rate is None:
                    missing.append({
                        "type": "payment",
                        "invoice_id": invoice.id,
                        "transaction_id": payment.id,
                        "currency": currency,
                        "date": paid_date.isoformat() if paid_date else None,
                    })
                    continue
                realized += fx_difference(
                    payment.amount, original_rate, settlement_rate, direction
                )

            for refund in refunds:
                refund_date = _date(refund.refunded_at)
                settlement_rate = _rate(db, owner_id, currency, base, refund_date)
                if settlement_rate is None:
                    missing.append({
                        "type": "refund",
                        "invoice_id": invoice.id,
                        "transaction_id": refund.id,
                        "currency": currency,
                        "date": refund_date.isoformat() if refund_date else None,
                    })
                    continue
                realized -= fx_difference(
                    refund.amount, original_rate, settlement_rate, direction
                )

        closing_rate = _rate(db, owner_id, currency, base, as_of)
        unrealized = ZERO
        outstanding_base = ZERO

        if outstanding:
            if closing_rate is None:
                missing.append({
                    "type": "closing",
                    "invoice_id": invoice.id,
                    "currency": currency,
                    "date": as_of.isoformat(),
                })
            else:
                outstanding_base = outstanding * closing_rate
                if original_rate is not None:
                    unrealized = (
                        outstanding
                        * (closing_rate - original_rate)
                        * Decimal(str(direction))
                    )

        values = result["invoices"]["by_currency"].setdefault(currency, {
            "invoiced": ZERO,
            "paid": ZERO,
            "refunded": ZERO,
            "credited": ZERO,
            "advance_allocated": ZERO,
            "outstanding": ZERO,
            "realized_fx": ZERO,
            "unrealized_fx": ZERO,
            "outstanding_base": ZERO,
        })
        values["invoiced"] += gross
        values["paid"] += paid
        values["refunded"] += refunded
        values["credited"] += credited
        values["advance_allocated"] += advanced
        values["outstanding"] += outstanding
        values["realized_fx"] += realized
        values["unrealized_fx"] += unrealized
        values["outstanding_base"] += outstanding_base

        result["invoices"]["invoice_count"] += 1
        result["invoices"]["realized_fx"] += realized
        result["invoices"]["unrealized_fx"] += unrealized
        result["invoices"]["outstanding_base"] += outstanding_base
        result["invoices"]["details"].append({
            "invoice_id": invoice.id,
            "direction": invoice.direction,
            "currency": currency,
            "invoice_date": invoice_date.isoformat() if invoice_date else None,
            "original_rate": str(original_rate) if original_rate is not None else None,
            "gross": str(gross),
            "paid": str(paid),
            "refunded": str(refunded),
            "credited": str(credited),
            "advance_allocated": str(advanced),
            "outstanding": str(outstanding),
            "outstanding_base": str(outstanding_base),
            "realized_fx": str(realized),
            "unrealized_fx": str(unrealized),
            "missing_rates": missing,
        })
        result["missing_rates"].extend(missing)

    transactions = (
        db.query(BankTransaction)
        .filter(BankTransaction.owner_id == owner_id)
        .order_by(BankTransaction.transaction_date, BankTransaction.id)
        .all()
    )

    for transaction in transactions:
        currency = (transaction.currency or base).upper()
        transaction_date = _date(transaction.transaction_date)
        rate = _rate(db, owner_id, currency, base, transaction_date)

        if rate is None:
            result["missing_rates"].append({
                "type": "bank_transaction",
                "transaction_id": transaction.id,
                "currency": currency,
                "date": transaction_date.isoformat() if transaction_date else None,
            })
            continue

        amount = dec(transaction.amount)
        converted = amount * rate
        values = result["bank_transactions"]["by_currency"].setdefault(currency, {
            "original_total": ZERO,
            "base_total": ZERO,
        })
        values["original_total"] += amount
        values["base_total"] += converted

        result["bank_transactions"]["transaction_count"] += 1
        result["bank_transactions"]["converted_total"] += converted
        result["bank_transactions"]["details"].append({
            "transaction_id": transaction.id,
            "date": transaction_date.isoformat() if transaction_date else None,
            "currency": currency,
            "amount": str(amount),
            "rate": str(rate),
            "base_amount": str(converted),
        })

    def serialize(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {key: serialize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [serialize(item) for item in value]
        return value

    return serialize(result)
