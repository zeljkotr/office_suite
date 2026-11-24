from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.invoice import Invoice


AGING_BUCKETS = (
    "current",
    "1_30",
    "31_60",
    "61_90",
    "over_90",
)


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _invoice_total(invoice: Invoice) -> Decimal:
    for name in ("adjusted_total", "total_amount", "total", "amount"):
        value = getattr(invoice, name, None)
        if value is not None:
            return _decimal(value)
    return Decimal("0")


def _paid(invoice: Invoice) -> Decimal:
    # Prefer the already established invoice-finance calculation.
    try:
        from app.services.invoice_finance import get_invoice_net_paid

        return _decimal(get_invoice_net_paid(invoice))
    except Exception:
        pass

    for name in ("net_paid", "paid_amount", "amount_paid", "total_paid"):
        value = getattr(invoice, name, None)
        if value is not None:
            return _decimal(value)

    return Decimal("0")


def _due_date(invoice: Invoice):
    return getattr(invoice, "due_date", None)


def _aging(due_date, as_of: date) -> tuple[str, int]:
    if not due_date:
        return "current", 0

    if hasattr(due_date, "date"):
        due_date = due_date.date()

    days = max(0, (as_of - due_date).days)

    if days == 0:
        return "current", days
    if days <= 30:
        return "1_30", days
    if days <= 60:
        return "31_60", days
    if days <= 90:
        return "61_90", days
    return "over_90", days


def build_receivables_payables(
    db: Session,
    *,
    owner_id: int | None = None,
    direction: str | None = None,
    status: str | None = None,
    aging: str | None = None,
    search: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()

    query = db.query(Invoice)

    if owner_id is not None and hasattr(Invoice, "uploaded_by"):
        query = query.filter(Invoice.uploaded_by == owner_id)

    if direction in ("incoming", "outgoing"):
        query = query.filter(Invoice.direction == direction)

    invoices = query.order_by(Invoice.due_date.asc(), Invoice.id.asc()).all()

    items: list[dict[str, Any]] = []

    for invoice in invoices:
        invoice_status = str(getattr(invoice, "status", "") or "").lower()

        # AR/AP represents approved financial invoices only.
        if invoice_status != "approved":
            continue

        total = _invoice_total(invoice)
        paid = _paid(invoice)
        outstanding = max(Decimal("0"), total - paid)

        invoice_status_value = "paid" if outstanding <= Decimal("0.005") else "open"

        due = _due_date(invoice)
        aging_bucket, overdue_days = _aging(due, as_of)

        if outstanding > Decimal("0.005") and overdue_days > 0:
            invoice_status_value = "overdue"

        party = (
            getattr(invoice, "customer_name", None)
            or getattr(invoice, "supplier_name", None)
            or getattr(invoice, "counterparty", None)
            or getattr(invoice, "vendor_name", None)
            or "—"
        )

        number = (
            getattr(invoice, "invoice_number", None)
            or getattr(invoice, "number", None)
            or str(invoice.id)
        )

        if status and status.lower() != invoice_status_value:
            continue

        if aging and aging != aging_bucket:
            continue

        if search:
            needle = search.lower()
            if needle not in str(party).lower() and needle not in str(number).lower():
                continue

        items.append(
            {
                "id": invoice.id,
                "direction": invoice.direction,
                "invoice_number": number,
                "party": party,
                "invoice_date": getattr(invoice, "invoice_date", None),
                "due_date": due,
                "currency": (
                    getattr(invoice, "currency_code", None)
                    or getattr(invoice, "currency", None)
                    or ""
                ),
                "total": str(total),
                "paid": str(paid),
                "outstanding": str(outstanding),
                "aging": aging_bucket,
                "overdue_days": overdue_days,
                "status": invoice_status_value,
            }
        )

    def total(field: str, dir_: str | None = None) -> Decimal:
        return sum(
            (
                _decimal(item[field])
                for item in items
                if dir_ is None or item["direction"] == dir_
            ),
            Decimal("0"),
        )

    def count(dir_: str | None = None, status_: str | None = None) -> int:
        return sum(
            1
            for item in items
            if (dir_ is None or item["direction"] == dir_)
            and (status_ is None or item["status"] == status_)
        )

    aging_summary: dict[str, dict[str, str]] = {}

    for bucket in AGING_BUCKETS:
        aging_summary[bucket] = {
            "receivables": str(
                sum(
                    (
                        _decimal(i["outstanding"])
                        for i in items
                        if i["direction"] == "outgoing"
                        and i["aging"] == bucket
                    ),
                    Decimal("0"),
                )
            ),
            "payables": str(
                sum(
                    (
                        _decimal(i["outstanding"])
                        for i in items
                        if i["direction"] == "incoming"
                        and i["aging"] == bucket
                    ),
                    Decimal("0"),
                )
            ),
        }

    return {
        "as_of": as_of.isoformat(),
        "summary": {
            "receivables": {
                "invoiced": str(total("total", "outgoing")),
                "paid": str(total("paid", "outgoing")),
                "outstanding": str(total("outstanding", "outgoing")),
                "overdue": str(
                    sum(
                        (
                            _decimal(i["outstanding"])
                            for i in items
                            if i["direction"] == "outgoing"
                            and i["status"] == "overdue"
                        ),
                        Decimal("0"),
                    )
                ),
                "open_count": count("outgoing", "open"),
                "overdue_count": count("outgoing", "overdue"),
            },
            "payables": {
                "invoiced": str(total("total", "incoming")),
                "paid": str(total("paid", "incoming")),
                "outstanding": str(total("outstanding", "incoming")),
                "overdue": str(
                    sum(
                        (
                            _decimal(i["outstanding"])
                            for i in items
                            if i["direction"] == "incoming"
                            and i["status"] == "overdue"
                        ),
                        Decimal("0"),
                    )
                ),
                "open_count": count("incoming", "open"),
                "overdue_count": count("incoming", "overdue"),
            },
        },
        "aging": aging_summary,
        "items": items,
    }
