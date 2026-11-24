from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastapi import HTTPException
import unittest
from decimal import Decimal

from app.services.invoice_finance import calculate_invoice_balance


class InvoiceFinanceTests(unittest.TestCase):
    def test_unpaid_invoice(self):
        result = calculate_invoice_balance("120.00", "0.00")
        self.assertEqual(result["outstanding_amount"], Decimal("120.00"))
        self.assertEqual(result["overpaid_amount"], Decimal("0"))
        self.assertEqual(result["payment_status"], "unpaid")

    def test_partially_paid_invoice(self):
        result = calculate_invoice_balance("120.00", "45.00")
        self.assertEqual(result["outstanding_amount"], Decimal("75.00"))
        self.assertEqual(result["payment_status"], "partially_paid")

    def test_fully_paid_invoice(self):
        result = calculate_invoice_balance("120.00", "120.00")
        self.assertEqual(result["outstanding_amount"], Decimal("0"))
        self.assertEqual(result["payment_status"], "paid")

    def test_overpayment_is_separated_from_outstanding(self):
        result = calculate_invoice_balance("120.00", "135.00")
        self.assertEqual(result["outstanding_amount"], Decimal("0"))
        self.assertEqual(result["overpaid_amount"], Decimal("15.00"))
        self.assertEqual(result["payment_status"], "paid")

    def test_advance_allocation_can_fully_pay_invoice(self):
        # Advance allocation is included in the invoice's net collected amount.
        result = calculate_invoice_balance("120.00", "120.00")
        self.assertEqual(result["outstanding_amount"], Decimal("0"))
        self.assertEqual(result["payment_status"], "paid")

    def test_advance_allocation_and_regular_payment_are_combined(self):
        # 70.00 advance allocation + 30.00 ordinary payment.
        result = calculate_invoice_balance("120.00", "100.00")
        self.assertEqual(result["outstanding_amount"], Decimal("20.00"))
        self.assertEqual(result["payment_status"], "partially_paid")

    def test_refund_reduces_net_collected_amount(self):
        # 100.00 received - 25.00 refunded = 75.00 net collected.
        result = calculate_invoice_balance("120.00", "75.00")
        self.assertEqual(result["outstanding_amount"], Decimal("45.00"))
        self.assertEqual(result["payment_status"], "partially_paid")

    def test_credit_note_reduces_invoice_total_before_balance_calculation(self):
        # Original invoice 120.00 less 30.00 credit note = 90.00 adjusted total.
        result = calculate_invoice_balance("90.00", "75.00")
        self.assertEqual(result["outstanding_amount"], Decimal("15.00"))
        self.assertEqual(result["payment_status"], "partially_paid")


if __name__ == "__main__":
    unittest.main()


class InvoiceAdvanceAPITests(unittest.TestCase):
    from app.api import invoices as api

    class Query:
        def __init__(self, value):
            self.value = value
        def filter(self, *args, **kwargs):
            return self
        def with_for_update(self):
            return self
        def first(self):
            return self.value

    def setUp(self):
        from app.api import invoices as api
        self.api = api
        self.admin = SimpleNamespace(id=7)
        self.advance = SimpleNamespace(
            id=1, direction="incoming", currency="EUR",
            party_name="Example Ltd", amount=Decimal("100.00"),
        )
        self.invoice = SimpleNamespace(
            id=10, status="approved", direction="incoming",
            currency="EUR", supplier_name="Example Ltd",
            payment_status="unpaid", paid_by=None,
        )
        self.db = Mock()
        self.db.query.side_effect = lambda model: self.Query(
            self.advance if model is api.InvoiceAdvance else self.invoice
        )

    def allocate(self, amount):
        payload = self.api.InvoiceAdvanceAllocationCreateRequest(
            invoice_id=10, amount=Decimal(amount)
        )
        return self.api.allocate_invoice_advance(
            1, payload, self.db, self.admin
        )

    def test_allocation_succeeds_and_updates_invoice_status(self):
        with patch.object(self.api, "get_advance_balances", return_value={
            "available_amount": Decimal("100.00")
        }), patch.object(self.api, "get_invoice_net_paid", return_value=Decimal("0")), \
             patch.object(self.api, "get_invoice_adjusted_total", return_value=Decimal("60.00")), \
             patch.object(self.api, "record_invoice_event"):
            result = self.allocate("60.00")
        self.assertEqual(result.amount, Decimal("60.00"))
        self.assertEqual(self.invoice.payment_status, "paid")
        self.db.commit.assert_called_once()

    def test_allocation_rejects_direction_mismatch(self):
        self.invoice.direction = "outgoing"
        with self.assertRaises(HTTPException) as caught:
            self.allocate("10.00")
        self.assertEqual(caught.exception.status_code, 422)

    def test_allocation_rejects_currency_mismatch(self):
        self.invoice.currency = "USD"
        with self.assertRaises(HTTPException) as caught:
            self.allocate("10.00")
        self.assertEqual(caught.exception.status_code, 422)

    def test_allocation_rejects_partner_mismatch(self):
        self.invoice.supplier_name = "Other Ltd"
        with self.assertRaises(HTTPException) as caught:
            self.allocate("10.00")
        self.assertEqual(caught.exception.status_code, 422)

    def test_allocation_rejects_unapproved_invoice(self):
        self.invoice.status = "pending"
        with self.assertRaises(HTTPException) as caught:
            self.allocate("10.00")
        self.assertEqual(caught.exception.status_code, 409)

    def test_allocation_cannot_exceed_available_advance(self):
        with patch.object(self.api, "get_advance_balances", return_value={
            "available_amount": Decimal("20.00")
        }):
            with self.assertRaises(HTTPException) as caught:
                self.allocate("25.00")
        self.assertEqual(caught.exception.status_code, 422)
        self.db.commit.assert_not_called()

    def test_allocation_cannot_exceed_invoice_outstanding_balance(self):
        with patch.object(self.api, "get_advance_balances", return_value={
            "available_amount": Decimal("100.00")
        }), patch.object(self.api, "get_invoice_net_paid", return_value=Decimal("80.00")), \
             patch.object(self.api, "get_invoice_adjusted_total", return_value=Decimal("100.00")):
            with self.assertRaises(HTTPException) as caught:
                self.allocate("25.00")
        self.assertEqual(caught.exception.status_code, 422)
        self.db.commit.assert_not_called()

    def test_refund_succeeds_within_unallocated_balance(self):
        payload = self.api.InvoiceAdvanceRefundCreateRequest(
            amount=Decimal("30.00"), reason="Customer refund"
        )
        with patch.object(self.api, "get_advance_balances", return_value={
            "available_amount": Decimal("40.00")
        }), patch.object(self.api, "record_advance_event"):
            result = self.api.refund_invoice_advance(
                1, payload, self.db, self.admin
            )
        self.assertEqual(result.amount, Decimal("30.00"))
        self.db.commit.assert_called_once()

    def test_refund_cannot_exceed_unallocated_balance(self):
        payload = self.api.InvoiceAdvanceRefundCreateRequest(
            amount=Decimal("41.00")
        )
        with patch.object(self.api, "get_advance_balances", return_value={
            "available_amount": Decimal("40.00")
        }):
            with self.assertRaises(HTTPException) as caught:
                self.api.refund_invoice_advance(
                    1, payload, self.db, self.admin
                )
        self.assertEqual(caught.exception.status_code, 422)
        self.db.commit.assert_not_called()
