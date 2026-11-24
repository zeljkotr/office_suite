from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException

from app.services.ai_email import extract_invoice_json, select_supported_gemini_model
from app.services.invoice_processing import process_invoice
from app.services.invoice_validation import validate_invoice


def valid_result():
    return {
        "supplier_name": "Example d.o.o.", "supplier_address": "Belgrade",
        "supplier_tax_id": "123456789", "supplier_email": "ap@example.test",
        "invoice_number": "INV-1", "invoice_date": "2026-01-02",
        "due_date": "2026-02-02", "currency": "RSD", "subtotal": 100,
        "tax_rate": 20, "tax_amount": 20, "total_amount": 120,
        "line_items": [{"description": "Service", "quantity": 1,
                         "unit_price": 100, "tax_rate": 20, "line_total": 120}],
    }


class InvoiceAITests(unittest.TestCase):
 def test_any_three_letter_currency_is_supported_without_usd_fallback(self):
    data = valid_result()
    data["currency"] = "XTS"
    result = validate_invoice(data)
    self.assertTrue(result["valid"])
    data["currency"] = None
    self.assertFalse(validate_invoice(data)["valid"])

 def test_select_only_current_generate_content_flash_lite(self):
    selected = select_supported_gemini_model([
        {"name": "models/gemini-pro-latest", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-flash-lite-preview", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-flash-lite-latest", "supportedGenerationMethods": ["generateContent"]},
    ])
    self.assertEqual(selected["name"], "models/gemini-flash-lite-latest")
    self.assertIsNone(select_supported_gemini_model([]))


 def test_extraction_rejects_missing_fields_and_invalid_line_items(self):
    db, user = Mock(), SimpleNamespace(id=1)
    with patch("app.services.ai_email._gemini_json", return_value={"supplier_name": "X"}):
        with self.assertRaises(HTTPException) as caught:
            extract_invoice_json(db, user, "OCR")
        self.assertIn("required JSON fields", caught.exception.detail)
    result = valid_result()
    result["line_items"] = [None]
    with patch("app.services.ai_email._gemini_json", return_value=result):
        with self.assertRaises(HTTPException) as caught:
            extract_invoice_json(db, user, "OCR")
        self.assertIn("invalid invoice line items", caught.exception.detail)


 def test_invalid_model_json_response_is_explicit_error(self):
    # Exercise the shared response parser without requiring a live Gemini key.
    from app.services import ai_email

    class Query:
        def filter(self, *args, **kwargs): return self
        def first(self): return SimpleNamespace(
            encrypted_api_key="encrypted", privacy_acknowledged_at=date.today(), model_name=None
        )

    class FakeDB:
        def query(self, *_): return Query()
        def commit(self): pass

    class Response:
        status_code = 200
        def json(self): return {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}

    with patch.object(ai_email, "decrypt_api_key", return_value="secret"), \
         patch.object(ai_email, "list_supported_gemini_models", return_value=[{"name": "models/gemini-flash-lite-live", "supportedGenerationMethods": ["generateContent"]}]), \
         patch.object(ai_email.httpx, "post", return_value=Response()):
        with self.assertRaises(HTTPException) as caught:
            ai_email._gemini_json(FakeDB(), SimpleNamespace(id=1), "prompt")
        self.assertIn("invalid response", caught.exception.detail)

 def test_missing_gemini_configuration_is_an_explicit_error(self):
    from app.services import ai_email

    class Query:
        def filter(self, *args, **kwargs): return self
        def first(self): return None

    class FakeDB:
        def query(self, *_): return Query()

    with self.assertRaises(HTTPException) as caught:
        ai_email._gemini_json(FakeDB(), SimpleNamespace(id=1), "prompt", content_type="invoice")
    self.assertIn("Gemini AI is not configured", caught.exception.detail)


 def test_validation_requires_known_currency_checks_totals_dates_and_lines(self):
    data = valid_result()
    data["currency"] = None
    data["due_date"] = date(2025, 12, 1)
    data["invoice_date"] = date(2026, 1, 2)
    data["line_items"][0]["line_total"] = Decimal("99")
    result = validate_invoice(data)
    self.assertFalse(result["valid"])
    self.assertTrue(any("Currency is missing" in error for error in result["errors"]))
    self.assertTrue(any("Due date cannot" in error for error in result["errors"]))
    self.assertTrue(any("Line item 1 total" in error for error in result["errors"]))


 def test_process_preserves_ocr_and_always_requires_human_review(self):
    extracted = valid_result()
    db, user = Mock(), SimpleNamespace(id=1)
    with patch("app.services.invoice_processing.extract_text", return_value="raw OCR"), \
         patch("app.services.invoice_processing.extract_invoice_data", return_value=extracted):
        result = process_invoice(b"document", "invoice.pdf", db, user)
    self.assertEqual(result["ocr_text"], "raw OCR")
    self.assertEqual(result["extracted_data"]["line_items"], extracted["line_items"])
    self.assertTrue(result["requires_human_review"])
    self.assertTrue(result["validation"]["requires_human_review"])


if __name__ == "__main__":
    unittest.main()
