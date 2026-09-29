from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from apps.core.numeric import (
    CurrencyMismatch,
    Money,
    Weight,
    round_money,
    round_weight,
    to_decimal,
)


class TestToDecimal:
    @pytest.mark.parametrize("value", [0.1, 1.0, True])
    def test_refuses_float_and_bool(self, value):
        with pytest.raises(TypeError):
            to_decimal(value)

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "abc", ""])
    def test_refuses_non_finite_and_garbage(self, value):
        with pytest.raises(ValueError):
            to_decimal(value)

    def test_accepts_str_int_decimal(self):
        assert to_decimal(" 12.50 ") == Decimal("12.50")
        assert to_decimal(7) == Decimal(7)
        assert to_decimal(Decimal("0.001")) == Decimal("0.001")


class TestRounding:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("2.345", "2.35"), ("2.344", "2.34"), ("-2.345", "-2.35"), ("0.005", "0.01")],
    )
    def test_money_rounds_half_up(self, value, expected):
        assert round_money(value) == Decimal(expected)

    def test_money_minor_units(self):
        assert round_money("1.2345", minor_units=3) == Decimal("1.235")
        assert round_money("1.5", minor_units=0) == Decimal("2")

    def test_weight_three_places(self):
        assert round_weight("10.0005") == Decimal("10.001")

    @given(st.decimals(min_value=-10**12, max_value=10**12, allow_nan=False, places=6))
    def test_round_money_is_idempotent_and_close(self, value):
        once = round_money(value)
        assert round_money(once) == once
        assert abs(once - value) <= Decimal("0.005")


class TestMoney:
    def test_same_currency_arithmetic(self):
        total = Money("10.10", "EGP") + Money("5.05", "EGP") - Money("0.15", "EGP")
        assert total == Money("15.00", "EGP")

    def test_currency_mismatch(self):
        with pytest.raises(CurrencyMismatch):
            Money("1", "EGP") + Money("1", "USD")

    def test_multiply_and_round(self):
        assert (Money("10", "USD") * "0.3333").rounded() == Money("3.33", "USD")
        assert (3 * Money("1.10", "USD")) == Money("3.30", "USD")

    def test_rejects_bad_currency_and_float(self):
        with pytest.raises(ValueError):
            Money("1", "egp")
        with pytest.raises(TypeError):
            Money(1.5, "EGP")


class TestWeight:
    def test_carats(self):
        assert Weight.from_carats("1.5").grams == Decimal("0.30")
        assert Weight("0.2").carats == Decimal("1")

    def test_arithmetic(self):
        assert (Weight("10.5") - Weight("0.25")).rounded() == Weight("10.250")


def test_num_filter_rounds_half_up():
    from apps.core.templatetags.ui import num

    assert num(Decimal("12.1125"), 3) == "12.113"
    assert num(Decimal("2.345"), 2) == "2.35"
    assert num(None) == "—"
