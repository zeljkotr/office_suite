import unittest
from decimal import Decimal

from app.services.financial_reports import (
    aggregate_signed_amounts,
    calculate_closing_balance,
    classify_payment_method,
    money,
    signed_record_amount,
)


class FinancialReportsTests(unittest.TestCase):
    def test_payment_method_classification(self):
        self.assertEqual(classify_payment_method("Cash"), "non_bank")
        self.assertEqual(classify_payment_method("Cash payment"), "non_bank")
        self.assertEqual(classify_payment_method("Bank transfer"), "bank")
        self.assertEqual(classify_payment_method("SEPA transfer"), "bank")
        self.assertEqual(classify_payment_method("Credit card"), "bank")
        self.assertEqual(classify_payment_method(None), "unclassified")
        self.assertEqual(classify_payment_method(""), "unclassified")
        self.assertEqual(classify_payment_method("Other"), "unclassified")

    def test_aggregate_signed_amounts(self):
        result = aggregate_signed_amounts([
            ("EUR", Decimal("100")),
            ("EUR", Decimal("-40")),
            ("EUR", Decimal("25")),
            ("RSD", Decimal("-1000")),
            ("", Decimal("500")),
        ])
        self.assertEqual(result["inflow"]["EUR"], "125")
        self.assertEqual(result["outflow"]["EUR"], "40")
        self.assertEqual(result["net"]["EUR"], "85")
        self.assertEqual(result["outflow"]["RSD"], "1000")
        self.assertNotIn("", result["net"])

    def test_closing_balance(self):
        self.assertEqual(
            calculate_closing_balance("1000", "250"),
            Decimal("1250"),
        )
        self.assertEqual(
            calculate_closing_balance("1000", "-250"),
            Decimal("750"),
        )

    def test_money_handles_none_and_decimal(self):
        self.assertEqual(money(None), Decimal("0"))
        self.assertEqual(money(Decimal("12.50")), Decimal("12.50"))

    def test_income_and_expense_signs(self):
        self.assertEqual(
            signed_record_amount("income", "125.50"),
            Decimal("125.50"),
        )
        self.assertEqual(
            signed_record_amount("expense", "25.50"),
            Decimal("-25.50"),
        )

    def test_unknown_record_type_is_rejected(self):
        with self.assertRaises(ValueError):
            signed_record_amount("refund", "10")


if __name__ == "__main__":
    unittest.main()
