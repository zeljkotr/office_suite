from app.services.ai_email import extract_invoice_json


def extract_invoice_data(text: str, db, user) -> dict:
    """Extract invoice fields from OCR text through the configured Gemini path."""
    return extract_invoice_json(db, user, text)
