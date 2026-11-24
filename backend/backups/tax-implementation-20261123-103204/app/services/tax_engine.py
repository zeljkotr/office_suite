from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
RATE_PRECISION = Decimal("0.000001")

SUPPORTED_TREATMENTS = {
    "standard",
    "exempt",
    "zero_rated",
    "out_of_scope",
    "reverse_charge",
}
SUPPORTED_PRICE_MODES = {"exclusive", "inclusive"}


def _decimal(value, field):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a valid decimal number.") from None

    if not result.is_finite():
        raise ValueError(f"{field} must be a finite number.")

    return result


def calculate_tax(amount, rate, price_mode="exclusive", treatment="standard"):
    base = _decimal(amount, "Amount")
    percentage = _decimal(rate, "Tax rate")

    if base < 0:
        raise ValueError("Amount cannot be negative.")
    if percentage < 0:
        raise ValueError("Tax rate cannot be negative.")
    if price_mode not in SUPPORTED_PRICE_MODES:
        raise ValueError("Unsupported price mode.")
    if treatment not in SUPPORTED_TREATMENTS:
        raise ValueError("Unsupported tax treatment.")

    if treatment != "standard":
        taxable = base
        tax = Decimal("0")
        total = base
    elif price_mode == "inclusive":
        total = base
        taxable = (
            total / (Decimal("1") + percentage / Decimal("100"))
            if percentage
            else total
        )
        tax = total - taxable
    else:
        taxable = base
        tax = taxable * percentage / Decimal("100")
        total = taxable + tax

    return {
        "taxable_amount": taxable.quantize(CENT, rounding=ROUND_HALF_UP),
        "tax_amount": tax.quantize(CENT, rounding=ROUND_HALF_UP),
        "total_amount": total.quantize(CENT, rounding=ROUND_HALF_UP),
        "rate": percentage.quantize(RATE_PRECISION, rounding=ROUND_HALF_UP),
        "treatment": treatment,
        "price_mode": price_mode,
    }


def rate_is_valid(rate, on_date=None):
    check_date = on_date or date.today()

    if not isinstance(check_date, date):
        raise ValueError("Rate validation date must be a date.")

    if not rate.is_active:
        return False

    if rate.valid_from and check_date < rate.valid_from:
        return False

    if rate.valid_to and check_date > rate.valid_to:
        return False

    return True
