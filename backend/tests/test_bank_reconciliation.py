import unittest
from datetime import date
from decimal import Decimal

from app.services.bank_reconciliation import (
    normalize_text, transaction_fingerprint, parse_amount,
)


class BankReconciliationServiceTests(unittest.TestCase):
    def test_normalize_text(self):
        self.assertEqual(normalize_text("  ACME   DOO "), "acme doo")

    def test_parse_amount_european(self):
        self.assertEqual(parse_amount("1.234,56"), Decimal("1234.56"))

    def test_parse_amount_decimal(self):
        self.assertEqual(parse_amount("1234.56"), Decimal("1234.56"))

    def test_fingerprint_is_deterministic(self):
        a = transaction_fingerprint(
            date(2026, 1, 1), Decimal("100.00"), "EUR",
            "Payment", "Acme", "REF", "TX1",
        )
        b = transaction_fingerprint(
            date(2026, 1, 1), Decimal("100.00"), "EUR",
            "Payment", "Acme", "REF", "TX1",
        )
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)


if __name__ == "__main__":
    unittest.main()
