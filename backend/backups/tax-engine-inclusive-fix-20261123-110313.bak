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

# Extended tax calculation and rule evaluation utilities.
# Existing calculate_tax() behavior remains unchanged.

def calculate_compound_tax(amount, rates, price_mode="exclusive", treatment="standard"):
    """
    Calculate multiple ordered tax rates.

    Each rate is a mapping with:
      - rate: percentage
      - is_compound: include preceding taxes in this rate's taxable base

    For inclusive pricing, the original gross amount is solved algebraically
    before rounding individual tax components.
    """
    base_amount = _decimal(amount, "Amount")
    if base_amount < 0:
        raise ValueError("Amount cannot be negative.")
    if price_mode not in SUPPORTED_PRICE_MODES:
        raise ValueError("Unsupported price mode.")
    if treatment not in SUPPORTED_TREATMENTS:
        raise ValueError("Unsupported tax treatment.")
    if not isinstance(rates, (list, tuple)) or not rates:
        raise ValueError("At least one tax rate is required.")

    normalized = []
    for index, item in enumerate(rates):
        if not isinstance(item, dict) or "rate" not in item:
            raise ValueError(f"Tax rate at position {index + 1} is invalid.")
        percentage = _decimal(item["rate"], f"Tax rate {index + 1}")
        if percentage < 0:
            raise ValueError("Tax rate cannot be negative.")
        normalized.append({
            "rate": percentage / Decimal("100"),
            "percentage": percentage,
            "is_compound": bool(item.get("is_compound", False)),
            "rate_id": item.get("rate_id"),
            "name": item.get("name"),
        })

    if treatment != "standard":
        zero = Decimal("0.00")
        return {
            "taxable_amount": base_amount.quantize(CENT, rounding=ROUND_HALF_UP),
            "tax_amount": zero,
            "total_amount": base_amount.quantize(CENT, rounding=ROUND_HALF_UP),
            "treatment": treatment,
            "price_mode": price_mode,
            "tax_breakdown": [
                {
                    "rate_id": item["rate_id"],
                    "name": item["name"],
                    "rate": item["percentage"].quantize(RATE_PRECISION),
                    "is_compound": item["is_compound"],
                    "taxable_amount": base_amount.quantize(CENT, rounding=ROUND_HALF_UP),
                    "tax_amount": zero,
                }
                for item in normalized
            ],
        }

    # Derive the gross multiplier for inclusive prices.
    multiplier = Decimal("1")
    for item in normalized:
        rate = item["rate"]
        if item["is_compound"]:
            multiplier *= Decimal("1") + rate
        else:
            multiplier += rate

    taxable = (
        base_amount / multiplier
        if price_mode == "inclusive" and multiplier
        else base_amount
    )

    accumulated_tax = Decimal("0")
    breakdown = []
    for item in normalized:
        component_base = taxable + (
            accumulated_tax if item["is_compound"] else Decimal("0")
        )
        component_tax = component_base * item["rate"]
        rounded_base = component_base.quantize(CENT, rounding=ROUND_HALF_UP)
        rounded_tax = component_tax.quantize(CENT, rounding=ROUND_HALF_UP)
        breakdown.append({
            "rate_id": item["rate_id"],
            "name": item["name"],
            "rate": item["percentage"].quantize(RATE_PRECISION, rounding=ROUND_HALF_UP),
            "is_compound": item["is_compound"],
            "taxable_amount": rounded_base,
            "tax_amount": rounded_tax,
        })
        accumulated_tax += component_tax

    rounded_taxable = taxable.quantize(CENT, rounding=ROUND_HALF_UP)
    rounded_tax = accumulated_tax.quantize(CENT, rounding=ROUND_HALF_UP)
    rounded_total = (
        base_amount.quantize(CENT, rounding=ROUND_HALF_UP)
        if price_mode == "inclusive"
        else (taxable + accumulated_tax).quantize(CENT, rounding=ROUND_HALF_UP)
    )

    return {
        "taxable_amount": rounded_taxable,
        "tax_amount": rounded_tax,
        "total_amount": rounded_total,
        "treatment": treatment,
        "price_mode": price_mode,
        "tax_breakdown": breakdown,
    }


_RULE_OPERATORS = {"$eq", "$ne", "$in", "$not_in", "$gte", "$gt", "$lte", "$lt", "$contains", "$exists"}


def matches_rule_conditions(conditions, context):
    """
    Safely evaluate a JSON rule condition object without eval or execution.

    Simple values mean equality. Operator objects support the allowlisted
    operators above. All supplied conditions must match.
    """
    if conditions in (None, {}):
        return True
    if not isinstance(conditions, dict) or not isinstance(context, dict):
        return False

    for field, expected in conditions.items():
        if not isinstance(field, str) or not field or field.startswith("$"):
            return False
        exists = field in context
        actual = context.get(field)

        if isinstance(expected, dict):
            if not expected or any(op not in _RULE_OPERATORS for op in expected):
                return False
            for operator, value in expected.items():
                try:
                    if operator == "$exists":
                        matched = exists is bool(value)
                    elif not exists:
                        matched = False
                    elif operator == "$eq":
                        matched = actual == value
                    elif operator == "$ne":
                        matched = actual != value
                    elif operator == "$in":
                        matched = isinstance(value, (list, tuple)) and actual in value
                    elif operator == "$not_in":
                        matched = isinstance(value, (list, tuple)) and actual not in value
                    elif operator == "$gte":
                        matched = actual >= value
                    elif operator == "$gt":
                        matched = actual > value
                    elif operator == "$lte":
                        matched = actual <= value
                    elif operator == "$lt":
                        matched = actual < value
                    elif operator == "$contains":
                        matched = value in actual
                    else:
                        matched = False
                except (TypeError, ValueError):
                    matched = False
                if not matched:
                    return False
        elif not exists or actual != expected:
            return False
    return True


def redact_sensitive_configuration(value):
    """Recursively redact credentials from nested integration configuration."""
    sensitive = ("secret", "password", "token", "api_key", "private_key", "credential")
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(term in normalized for term in sensitive):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_sensitive_configuration(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive_configuration(item) for item in value]
    return value
