import unittest
from decimal import Decimal

from app.services.financial_records import financial_status


class FinancialRecordServiceTests(unittest.TestCase):
    def test_payment_status(self):
        self.assertEqual(financial_status(Decimal("100"), Decimal("0")), "unpaid")
        self.assertEqual(financial_status(Decimal("100"), Decimal("40")), "partially_paid")
        self.assertEqual(financial_status(Decimal("100"), Decimal("100")), "paid")

    def test_payment_status_never_changes_invoice_finance(self):
        from app.services.invoice_finance import calculate_invoice_balance
        result = calculate_invoice_balance(100, 40)
        self.assertEqual(result["payment_status"], "partially_paid")
        self.assertEqual(result["outstanding_amount"], Decimal("60"))


if __name__ == "__main__":
    unittest.main()
