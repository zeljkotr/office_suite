from decimal import Decimal

from app.services.fx_accounting import fx_difference


def test_fx_difference_positive_receivable_gain():
    assert fx_difference(
        Decimal("100"), Decimal("1.00"), Decimal("1.10"), 1
    ) == Decimal("10.000")


def test_fx_difference_negative_receivable_loss():
    assert fx_difference(
        Decimal("100"), Decimal("1.10"), Decimal("1.00"), 1
    ) == Decimal("-10.000")


def test_fx_difference_payable_sign_is_reversed():
    assert fx_difference(
        Decimal("100"), Decimal("1.00"), Decimal("1.10"), -1
    ) == Decimal("-10.000")


def test_fx_difference_zero_when_rates_match():
    assert fx_difference(
        Decimal("125.50"), Decimal("117.20"), Decimal("117.20"), 1
    ) == Decimal("0.000")
