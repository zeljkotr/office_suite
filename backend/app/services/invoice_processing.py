import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.invoice_extraction import extract_invoice_data
from app.services.invoice_validation import validate_invoice
from app.services.ocr import extract_text


def process_invoice(file_bytes: bytes, filename: str, db, user) -> dict:
    if not file_bytes:
        raise ValueError("The uploaded document is empty.")

    fingerprint = hashlib.sha256(file_bytes).hexdigest()
    ocr_text = extract_text(file_bytes, filename)
    if not ocr_text.strip():
        raise ValueError("No text could be extracted from the uploaded document.")

    extracted = extract_invoice_data(ocr_text, db, user)
    validation_data = dict(extracted)
    invalid_dates = []
    for field in ("invoice_date", "due_date"):
        value = validation_data.get(field)
        if value:
            try:
                validation_data[field] = date.fromisoformat(value)
            except (ValueError, TypeError):
                invalid_dates.append(field)
                validation_data[field] = None

    for field in ("subtotal", "tax_amount", "total_amount"):
        value = validation_data.get(field)
        try:
            validation_data[field] = Decimal(str(value)) if value is not None else None
        except (InvalidOperation, ValueError, TypeError):
            validation_data[field] = value

    validation = validate_invoice(validation_data)
    for field in invalid_dates:
        validation["errors"].append(f"{field.replace('_', ' ').capitalize()} is not a valid ISO date.")
    if invalid_dates:
        validation["valid"] = False
    return {
        "fingerprint": fingerprint,
        "ocr_text": ocr_text,
        "extracted_data": extracted,
        "validation": validation,
        "requires_human_review": True,
    }
