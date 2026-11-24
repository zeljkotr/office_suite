import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.tax_engine import calculate_tax, rate_is_valid


class TaxEngineTests(unittest.TestCase):
    def test_exclusive_calculation(self):
        result = calculate_tax("100", "20")
        self.assertEqual(result["taxable_amount"], Decimal("100.00"))
        self.assertEqual(result["tax_amount"], Decimal("20.00"))
        self.assertEqual(result["total_amount"], Decimal("120.00"))

    def test_inclusive_calculation(self):
        result = calculate_tax("120", "20", "inclusive")
        self.assertEqual(result["taxable_amount"], Decimal("100.00"))
        self.assertEqual(result["tax_amount"], Decimal("20.00"))
        self.assertEqual(result["total_amount"], Decimal("120.00"))

    def test_zero_rate(self):
        result = calculate_tax("100", "0")
        self.assertEqual(result["tax_amount"], Decimal("0.00"))
        self.assertEqual(result["total_amount"], Decimal("100.00"))

    def test_non_standard_treatments_do_not_add_tax(self):
        for treatment in (
            "exempt", "zero_rated", "out_of_scope", "reverse_charge"
        ):
            with self.subTest(treatment=treatment):
                result = calculate_tax("100", "25", treatment=treatment)
                self.assertEqual(result["tax_amount"], Decimal("0.00"))
                self.assertEqual(result["total_amount"], Decimal("100.00"))

    def test_negative_amount_rejected(self):
        with self.assertRaises(ValueError):
            calculate_tax("-1", "20")

    def test_negative_rate_rejected(self):
        with self.assertRaises(ValueError):
            calculate_tax("100", "-1")

    def test_non_finite_values_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate_tax(value, "20")
                with self.assertRaises(ValueError):
                    calculate_tax("100", value)

    def test_invalid_price_mode_rejected(self):
        with self.assertRaises(ValueError):
            calculate_tax("100", "20", "unknown")

    def test_invalid_treatment_rejected(self):
        with self.assertRaises(ValueError):
            calculate_tax("100", "20", treatment="unknown")

    def test_rounding_is_half_up(self):
        result = calculate_tax("0.05", "10")
        self.assertEqual(result["tax_amount"], Decimal("0.01"))

    def test_rate_validity_boundaries(self):
        rate = SimpleNamespace(
            is_active=True,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
        self.assertTrue(rate_is_valid(rate, date(2026, 1, 1)))
        self.assertTrue(rate_is_valid(rate, date(2026, 12, 31)))
        self.assertFalse(rate_is_valid(rate, date(2025, 12, 31)))
        self.assertFalse(rate_is_valid(rate, date(2027, 1, 1)))

    def test_inactive_rate_is_invalid(self):
        rate = SimpleNamespace(
            is_active=False,
            valid_from=None,
            valid_to=None,
        )
        self.assertFalse(rate_is_valid(rate, date(2026, 6, 1)))


if __name__ == "__main__":
    unittest.main()
