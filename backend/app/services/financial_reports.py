from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.bank_reconciliation import (
    BankAccount,
    BankReconciliation,
    BankTransaction,
)
from app.models.financial_record import (
    FinancialRecord,
    FinancialRecordPayment,
)


BANK_PAYMENT_MARKERS = (
    "bank",
    "transfer",
    "wire",
    "sepa",
    "swift",
    "iban",
    "card",
    "direct debit",
)

NON_BANK_PAYMENT_METHODS = {
    "cash",
    "cash payment",
    "cash receipt",
    "petty cash",
    "check",
    "cheque",
}


def money(value) -> Decimal:
    return Decimal(str(value or 0))


def classify_payment_method(value: str | None) -> str:
    method = " ".join(str(value or "").strip().lower().split())

    if not method:
        return "unclassified"
    if method in NON_BANK_PAYMENT_METHODS:
        return "non_bank"
    if any(marker in method for marker in BANK_PAYMENT_MARKERS):
        return "bank"
    return "unclassified"


def signed_record_amount(record_type: str, amount) -> Decimal:
    value = money(amount)
    if record_type == "income":
        return value
    if record_type == "expense":
        return -value
    raise ValueError(f"Unsupported financial record type: {record_type}")


def aggregate_signed_amounts(
    rows: Iterable[tuple[str, object]],
) -> dict:
    inflow = defaultdict(Decimal)
    outflow = defaultdict(Decimal)

    for currency, raw_amount in rows:
        amount = money(raw_amount)
        currency = str(currency or "").strip().upper()

        if not currency:
            continue
        if amount > 0:
            inflow[currency] += amount
        elif amount < 0:
            outflow[currency] += abs(amount)

    currencies = sorted(set(inflow) | set(outflow))
    return {
        "inflow": {c: str(inflow[c]) for c in currencies},
        "outflow": {c: str(outflow[c]) for c in currencies},
        "net": {c: str(inflow[c] - outflow[c]) for c in currencies},
    }


def calculate_closing_balance(opening, net) -> Decimal:
    return money(opening) + money(net)


def cash_flow_report(
    db: Session,
    owner_id: int,
    is_superuser: bool,
    start_date,
    end_date,
    currency: str | None = None,
    account_id: int | None = None,
) -> dict:
    if start_date > end_date:
        raise ValueError("start_date must be before or equal to end_date")

    currency_filter = str(currency or "").strip().upper() or None

    account_query = db.query(BankAccount)
    if not is_superuser:
        account_query = account_query.filter(BankAccount.owner_id == owner_id)
    if account_id is not None:
        account_query = account_query.filter(BankAccount.id == account_id)
    if currency_filter:
        account_query = account_query.filter(
            BankAccount.currency == currency_filter
        )

    accounts = account_query.order_by(BankAccount.id).all()
    account_ids = [account.id for account in accounts]

    account_metrics = {}
    for account in accounts:
        account_metrics[account.id] = {
            "id": account.id,
            "name": account.name,
            "bank_name": account.bank_name,
            "iban": account.iban,
            "currency": str(account.currency or "").upper(),
            "opening_balance": money(account.opening_balance),
            "period_inflow": Decimal("0"),
            "period_outflow": Decimal("0"),
            "period_net": Decimal("0"),
            "closing_balance": money(account.opening_balance),
            "reconciliation_status": None,
            "reconciliation_difference": None,
            "reconciliation_period_end": None,
        }

    bank_transactions = []
    ignored_transaction_count = 0

    if account_ids:
        ignored_query = db.query(BankTransaction).filter(
            BankTransaction.account_id.in_(account_ids),
            BankTransaction.status == "ignored",
            BankTransaction.transaction_date >= start_date,
            BankTransaction.transaction_date <= end_date,
        )
        tx_query = db.query(BankTransaction).filter(
            BankTransaction.account_id.in_(account_ids),
            BankTransaction.status != "ignored",
            BankTransaction.transaction_date <= end_date,
        )

        if not is_superuser:
            ignored_query = ignored_query.filter(
                BankTransaction.owner_id == owner_id
            )
            tx_query = tx_query.filter(
                BankTransaction.owner_id == owner_id
            )

        if currency_filter:
            ignored_query = ignored_query.filter(
                BankTransaction.currency == currency_filter
            )
            tx_query = tx_query.filter(
                BankTransaction.currency == currency_filter
            )

        ignored_transaction_count = ignored_query.count()
        bank_transactions = tx_query.order_by(
            BankTransaction.transaction_date,
            BankTransaction.id,
        ).all()

    bank_period_rows = []
    transaction_count = 0
    matched_record_ids = set()

    for tx in bank_transactions:
        amount = money(tx.amount)
        tx_currency = str(tx.currency or "").strip().upper()

        if tx.transaction_date < start_date:
            if tx.account_id in account_metrics:
                account_metrics[tx.account_id]["opening_balance"] += amount
            continue

        if start_date <= tx.transaction_date <= end_date:
            bank_period_rows.append((tx_currency, amount))
            transaction_count += 1

            if tx.matched_record_id is not None:
                matched_record_ids.add(tx.matched_record_id)

            metrics = account_metrics.get(tx.account_id)
            if metrics is not None:
                if amount > 0:
                    metrics["period_inflow"] += amount
                elif amount < 0:
                    metrics["period_outflow"] += abs(amount)
                metrics["period_net"] += amount

    bank_totals = aggregate_signed_amounts(bank_period_rows)

    opening_by_currency = defaultdict(Decimal)
    closing_by_currency = defaultdict(Decimal)

    for metrics in account_metrics.values():
        cur = metrics["currency"]
        opening_by_currency[cur] += metrics["opening_balance"]

        closing = calculate_closing_balance(
            metrics["opening_balance"],
            metrics["period_net"],
        )
        metrics["closing_balance"] = closing
        closing_by_currency[cur] += closing

    # Reconciliation ownership is inherited through the already-authorized
    # BankAccount IDs. BankReconciliation has no owner_id column.
    reconciliation_map = {}
    if account_ids:
        reconciliation_query = db.query(BankReconciliation).filter(
            BankReconciliation.account_id.in_(account_ids),
            BankReconciliation.period_end <= end_date,
        )
        reconciliations = reconciliation_query.order_by(
            BankReconciliation.period_end.desc(),
            BankReconciliation.id.desc(),
        ).all()

        for reconciliation in reconciliations:
            if reconciliation.account_id not in reconciliation_map:
                reconciliation_map[reconciliation.account_id] = reconciliation

    for account_id_value, metrics in account_metrics.items():
        reconciliation = reconciliation_map.get(account_id_value)
        if reconciliation is not None:
            metrics["reconciliation_status"] = reconciliation.status
            metrics["reconciliation_difference"] = str(
                money(reconciliation.difference)
            )
            metrics["reconciliation_period_end"] = (
                reconciliation.period_end.isoformat()
            )

    payment_query = (
        db.query(FinancialRecordPayment, FinancialRecord)
        .join(
            FinancialRecord,
            FinancialRecord.id == FinancialRecordPayment.record_id,
        )
        .filter(
            FinancialRecordPayment.payment_date >= start_date,
            FinancialRecordPayment.payment_date <= end_date,
        )
    )

    if not is_superuser:
        payment_query = payment_query.filter(
            FinancialRecordPayment.owner_id == owner_id,
            FinancialRecord.owner_id == owner_id,
        )
    if currency_filter:
        payment_query = payment_query.filter(
            FinancialRecord.currency == currency_filter
        )

    recorded_payments = payment_query.all()

    non_bank_rows = []
    unclassified_rows = []
    bank_recorded_unmatched_rows = []

    for payment, record in recorded_payments:
        classification = classify_payment_method(payment.payment_method)
        cur = str(record.currency or "").strip().upper()
        signed_amount = signed_record_amount(
            record.record_type,
            payment.amount,
        )

        if classification == "non_bank":
            non_bank_rows.append((cur, signed_amount))
        elif classification == "unclassified":
            unclassified_rows.append((cur, signed_amount))
        elif record.id not in matched_record_ids:
            # A bank-method payment without a linked bank transaction is
            # exposed separately, not silently added to bank totals.
            bank_recorded_unmatched_rows.append((cur, signed_amount))

    non_bank_totals = aggregate_signed_amounts(non_bank_rows)
    unclassified_totals = aggregate_signed_amounts(unclassified_rows)
    bank_recorded_unmatched_totals = aggregate_signed_amounts(
        bank_recorded_unmatched_rows
    )

    total_inflow = defaultdict(Decimal)
    total_outflow = defaultdict(Decimal)

    for source in (bank_totals, non_bank_totals):
        for cur, value in source["inflow"].items():
            total_inflow[cur] += money(value)
        for cur, value in source["outflow"].items():
            total_outflow[cur] += money(value)

    currencies = sorted(
        set(total_inflow)
        | set(total_outflow)
        | set(opening_by_currency)
        | set(closing_by_currency)
        | set(unclassified_totals["inflow"])
        | set(unclassified_totals["outflow"])
        | set(bank_recorded_unmatched_totals["inflow"])
        | set(bank_recorded_unmatched_totals["outflow"])
    )

    return {
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "cash_flow": {
            "inflow_by_currency": {
                cur: str(total_inflow[cur]) for cur in currencies
            },
            "outflow_by_currency": {
                cur: str(total_outflow[cur]) for cur in currencies
            },
            "net_by_currency": {
                cur: str(total_inflow[cur] - total_outflow[cur])
                for cur in currencies
            },
        },
        "bank": {
            "opening_balance_by_currency": {
                cur: str(opening_by_currency[cur]) for cur in currencies
            },
            "closing_balance_by_currency": {
                cur: str(closing_by_currency[cur]) for cur in currencies
            },
            "inflow_by_currency": bank_totals["inflow"],
            "outflow_by_currency": bank_totals["outflow"],
            "net_by_currency": bank_totals["net"],
            "transaction_count": transaction_count,
            "ignored_transaction_count": ignored_transaction_count,
        },
        "non_bank_recorded": {
            "inflow_by_currency": non_bank_totals["inflow"],
            "outflow_by_currency": non_bank_totals["outflow"],
            "net_by_currency": non_bank_totals["net"],
        },
        "bank_recorded_unmatched": {
            "inflow_by_currency": bank_recorded_unmatched_totals["inflow"],
            "outflow_by_currency": bank_recorded_unmatched_totals["outflow"],
            "net_by_currency": bank_recorded_unmatched_totals["net"],
        },
        "unclassified_recorded_payments": {
            "inflow_by_currency": unclassified_totals["inflow"],
            "outflow_by_currency": unclassified_totals["outflow"],
            "net_by_currency": unclassified_totals["net"],
        },
        "accounts": [
            {
                "id": metrics["id"],
                "name": metrics["name"],
                "bank_name": metrics["bank_name"],
                "iban": metrics["iban"],
                "currency": metrics["currency"],
                "opening_balance": str(metrics["opening_balance"]),
                "period_inflow": str(metrics["period_inflow"]),
                "period_outflow": str(metrics["period_outflow"]),
                "period_net": str(metrics["period_net"]),
                "closing_balance": str(metrics["closing_balance"]),
                "reconciliation_status": metrics["reconciliation_status"],
                "reconciliation_difference": metrics[
                    "reconciliation_difference"
                ],
                "reconciliation_period_end": metrics[
                    "reconciliation_period_end"
                ],
            }
            for metrics in account_metrics.values()
        ],
    }
