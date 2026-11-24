from decimal import Decimal


ZERO = Decimal("0")


def calculate_invoice_balance(total_amount, paid_amount):
    total = Decimal(str(total_amount or 0))
    paid = Decimal(str(paid_amount or 0))

    outstanding = max(ZERO, total - paid)
    overpaid = max(ZERO, paid - total)

    if paid <= ZERO:
        payment_status = "unpaid"
    elif outstanding == ZERO:
        payment_status = "paid"
    else:
        payment_status = "partially_paid"

    return {
        "total_amount": total,
        "paid_amount": paid,
        "outstanding_amount": outstanding,
        "overpaid_amount": overpaid,
        "payment_status": payment_status,
    }
