import csv
import hashlib
import io
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bank_reconciliation import BankTransaction
from app.models.financial_record import FinancialRecord


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def transaction_fingerprint(
    transaction_date,
    amount,
    currency,
    description=None,
    counterparty=None,
    reference=None,
    external_id=None,
):
    if external_id:
        raw = f"external|{normalize_text(external_id)}"
    else:
        raw = "|".join([
            transaction_date.isoformat(),
            str(Decimal(str(amount)).quantize(Decimal("0.01"))),
            str(currency).upper(),
            normalize_text(description),
            normalize_text(counterparty),
            normalize_text(reference),
        ])
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_amount(value):
    value = str(value or "").strip().replace(" ", "")
    if not value:
        raise ValueError("Amount is required.")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {value}") from exc


def parse_date(value):
    value = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return date.fromisoformat(value) if fmt == "%Y-%m-%d" else date.strptime(value, fmt)
        except (ValueError, AttributeError):
            pass
    from datetime import datetime
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid date: {value}")


def csv_rows(content):
    text = content.decode("utf-8-sig")
    sample = text[:4096]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return list(reader)


def pick(row, *names):
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def parse_csv_transaction(row, account_currency):
    transaction_date = parse_date(
        pick(row, "date", "transaction_date", "datum", "booking_date")
    )
    amount_value = pick(row, "amount", "iznos", "value")
    if amount_value is None:
        credit = pick(row, "credit", "income", "priliv", "potrazuje")
        debit = pick(row, "debit", "expense", "odliv", "duguje")
        if credit:
            amount_value = parse_amount(credit)
        elif debit:
            amount_value = -abs(parse_amount(debit))
        else:
            raise ValueError("CSV row has no amount.")
    else:
        amount_value = parse_amount(amount_value)

    currency = (pick(row, "currency", "valuta", "ccy") or account_currency).strip().upper()
    return {
        "transaction_date": transaction_date,
        "value_date": (
            parse_date(pick(row, "value_date", "valuta_date", "datum_valute"))
            if pick(row, "value_date", "valuta_date", "datum_valute") else None
        ),
        "amount": amount_value,
        "currency": currency,
        "description": pick(row, "description", "opis", "purpose", "narrative"),
        "counterparty": pick(row, "counterparty", "partner", "payer", "payee", "nalogodavac"),
        "reference": pick(row, "reference", "referenca", "model", "poziv_na_broj"),
        "external_id": pick(row, "external_id", "transaction_id", "id", "unique_id"),
    }


def matching_candidates(db: Session, transaction: BankTransaction, limit=10):
    record_type = "income" if transaction.amount > 0 else "expense"
    amount = abs(Decimal(str(transaction.amount)))
    start = transaction.transaction_date - timedelta(days=5)
    end = transaction.transaction_date + timedelta(days=5)

    query = db.query(FinancialRecord).filter(
        FinancialRecord.owner_id == transaction.owner_id,
        FinancialRecord.record_type == record_type,
        FinancialRecord.currency == transaction.currency,
        FinancialRecord.record_date >= start,
        FinancialRecord.record_date <= end,
    )

    rows = query.all()
    scored = []
    tx_counterparty = normalize_text(transaction.counterparty)
    tx_description = normalize_text(transaction.description)
    tx_reference = normalize_text(transaction.reference)

    for item in rows:
        score = 0
        record_amount = Decimal(str(item.amount))
        if record_amount == amount:
            score += 60
        else:
            paid = db.query(func.coalesce(
                func.sum(
                    __import__(
                        "app.models.financial_record",
                        fromlist=["FinancialRecordPayment"],
                    ).FinancialRecordPayment.amount
                ), 0
            )).filter(
                __import__(
                    "app.models.financial_record",
                    fromlist=["FinancialRecordPayment"],
                ).FinancialRecordPayment.record_id == item.id
            ).scalar()
            outstanding = record_amount - Decimal(str(paid or 0))
            if outstanding == amount:
                score += 55

        if tx_counterparty and tx_counterparty in normalize_text(item.counterparty):
            score += 20
        if tx_description and tx_description in normalize_text(item.description):
            score += 10
        if tx_reference and tx_reference in normalize_text(item.document_number):
            score += 15
        day_delta = abs((item.record_date - transaction.transaction_date).days)
        score += max(0, 10 - day_delta)

        if score:
            scored.append((score, item))

    scored.sort(key=lambda x: (-x[0], x[1].record_date, x[1].id))
    return [
        {
            "financial_record_id": item.id,
            "score": score,
            "record_type": item.record_type,
            "record_date": item.record_date.isoformat(),
            "amount": str(item.amount),
            "currency": item.currency,
            "category": item.category,
            "counterparty": item.counterparty,
            "document_number": item.document_number,
        }
        for score, item in scored[:limit]
    ]
