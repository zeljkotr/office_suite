import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

ISO_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
MONEY_TOLERANCE = Decimal("0.02")


def _decimal(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def validate_invoice(data: dict) -> dict:
    errors, warnings = [], []
    for field, label in (("supplier_name", "Supplier name"), ("invoice_number", "Invoice number")):
        if not str(data.get(field) or "").strip():
            errors.append(f"{label} is missing.")

    currency = str(data.get("currency") or "").upper().strip()
    if not currency:
        errors.append("Currency is missing or could not be identified.")
    elif not ISO_CURRENCY_CODE.fullmatch(currency):
        errors.append("Currency must be a valid three-letter ISO 4217 code.")

    amounts = {}
    for field in ("subtotal", "tax_amount", "total_amount"):
        value = _decimal(data.get(field))
        if value is None:
            errors.append(f"{field.replace('_', ' ').capitalize()} is missing or is not a valid monetary amount.")
        elif value < 0:
            errors.append(f"{field} cannot be negative.")
        else:
            amounts[field] = value
    calculated_total = None
    if "subtotal" in amounts and "tax_amount" in amounts:
        calculated_total = amounts["subtotal"] + amounts["tax_amount"]
        if "total_amount" in amounts and abs(calculated_total - amounts["total_amount"]) > MONEY_TOLERANCE:
            errors.append("Invoice total does not match subtotal plus tax amount.")

    invoice_date, due_date = _date(data.get("invoice_date")), _date(data.get("due_date"))
    if invoice_date and due_date and due_date < invoice_date:
        errors.append("Due date cannot be earlier than invoice date.")
    if invoice_date and invoice_date > date.today():
        warnings.append("Invoice date is in the future.")
    if not invoice_date:
        warnings.append("Invoice date requires manual review.")
    if not due_date:
        warnings.append("Due date was not identified.")

    tax_id = str(data.get("supplier_tax_id") or "").strip()
    if not tax_id:
        warnings.append("Supplier tax ID is missing.")
    elif tax_id.upper().startswith("RS") or "serbia" in str(data.get("supplier_address") or "").lower():
        digits = re.sub(r"\D", "", tax_id)
        if len(digits) != 9:
            errors.append("Serbian PIB/VAT ID should contain 9 digits (optionally prefixed by RS).")

    line_items = data.get("line_items") or []
    for index, item in enumerate(line_items, start=1):
        if not isinstance(item, dict):
            errors.append(f"Line item {index} is invalid.")
            continue
        quantity = _decimal(item.get("quantity"))
        unit_price = _decimal(item.get("unit_price"))
        line_total = _decimal(item.get("line_total"))
        rate = _decimal(item.get("tax_rate"))
        if not str(item.get("description") or "").strip():
            warnings.append(f"Line item {index} description requires review.")
        if quantity is None or unit_price is None or line_total is None:
            warnings.append(f"Line item {index} has missing quantity or amount fields.")
        elif quantity < 0 or unit_price < 0 or line_total < 0:
            errors.append(f"Line item {index} contains a negative quantity or amount.")
        else:
            base = quantity * unit_price
            if abs(base - line_total) > MONEY_TOLERANCE and (rate is None or abs(base * (1 + rate / 100) - line_total) > MONEY_TOLERANCE):
                errors.append(f"Line item {index} total does not match quantity and unit price (with stated tax rate).")
        if rate is not None and not Decimal("0") <= rate <= Decimal("100"):
            errors.append(f"Line item {index} tax rate must be between 0 and 100.")

    if data.get("tax_rate") is not None:
        rate = _decimal(data.get("tax_rate"))
        if rate is None or not Decimal("0") <= rate <= Decimal("100"):
            errors.append("Tax rate must be between 0 and 100.")
    if not data.get("supplier_tax_id"):
        warnings.append("Confirm the supplier tax ID before approval.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": list(dict.fromkeys(warnings)),
        "requires_human_review": True,
        "calculated_total": str(calculated_total) if calculated_total is not None else None,
    }
