"""Decimal arithmetic for money and metal (§6.4, §8.6). The only place rounding is defined.

Floats are refused everywhere: `to_decimal(0.1)` raises. Build values from str, int or Decimal.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Storage steps, matching the column precisions in §6.4.
MONEY = Decimal("0.01")
MONEY_HIGH_PRECISION = Decimal("0.000001")  # ledger functional amounts, FX intermediates
UNIT_PRICE = Decimal("0.0001")  # price per gram / per carat, making charge per gram
WEIGHT = Decimal("0.001")  # gross / metal weight in grams
FINE_WEIGHT = Decimal("0.0001")  # fine grams and 21k-equivalent grams
CARAT = Decimal("0.001")
FINENESS = Decimal("0.001")  # per mille
FX_RATE = Decimal("0.00000001")
RATE = Decimal("0.000001")  # fractions: discount, fee, commission, markup (0.125 = 12.5 %)

GRAMS_PER_CARAT = Decimal("0.2")

ZERO = Decimal("0")


def configure_decimal_context() -> None:
    """Called once at startup (CoreConfig.ready). New threads copy DefaultContext."""
    for ctx in (decimal.DefaultContext, decimal.getcontext()):
        ctx.prec = 34
        ctx.rounding = decimal.ROUND_HALF_EVEN  # arithmetic; storage rounding is explicit below
        ctx.traps[InvalidOperation] = True
        ctx.traps[decimal.DivisionByZero] = True
        ctx.traps[decimal.Overflow] = True


def to_decimal(value: Decimal | int | str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"Refusing {type(value).__name__} {value!r}: use str, int or Decimal.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"Not a decimal number: {value!r}") from exc
    else:
        raise TypeError(f"Cannot convert {type(value).__name__} to Decimal.")
    if not result.is_finite():
        raise ValueError(f"Not a finite number: {value!r}")
    return result


def quantize(value: Decimal | int | str, step: Decimal) -> Decimal:
    """Round half away from zero to `step` (e.g. MONEY, WEIGHT)."""
    return to_decimal(value).quantize(step, rounding=ROUND_HALF_UP)


def round_money(value: Decimal | int | str, minor_units: int = 2) -> Decimal:
    return quantize(value, Decimal(1).scaleb(-minor_units))


def round_weight(value: Decimal | int | str) -> Decimal:
    return quantize(value, WEIGHT)


def round_fine_weight(value: Decimal | int | str) -> Decimal:
    return quantize(value, FINE_WEIGHT)


def round_rate(value: Decimal | int | str) -> Decimal:
    return quantize(value, RATE)


class CurrencyMismatch(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self):
        object.__setattr__(self, "amount", to_decimal(self.amount))
        if not (len(self.currency) == 3 and self.currency.isalpha() and self.currency.isupper()):
            raise ValueError(f"Currency must be an ISO 4217 code, got {self.currency!r}")

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(ZERO, currency)

    def _same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"Expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: Decimal | int | str) -> Money:
        return Money(self.amount * to_decimal(factor), self.currency)

    __rmul__ = __mul__

    def rounded(self, minor_units: int = 2) -> Money:
        return Money(round_money(self.amount, minor_units), self.currency)

    def __str__(self):
        return f"{self.amount} {self.currency}"


@dataclass(frozen=True, slots=True)
class Weight:
    """A mass in grams. Metal balances use fine grams (see apps.catalog.domain.metal)."""

    grams: Decimal

    def __post_init__(self):
        object.__setattr__(self, "grams", to_decimal(self.grams))

    @classmethod
    def from_carats(cls, carats: Decimal | int | str) -> Weight:
        return cls(to_decimal(carats) * GRAMS_PER_CARAT)

    @property
    def carats(self) -> Decimal:
        return self.grams / GRAMS_PER_CARAT

    def __add__(self, other: Weight) -> Weight:
        if not isinstance(other, Weight):
            raise TypeError(f"Expected Weight, got {type(other).__name__}")
        return Weight(self.grams + other.grams)

    def __sub__(self, other: Weight) -> Weight:
        if not isinstance(other, Weight):
            raise TypeError(f"Expected Weight, got {type(other).__name__}")
        return Weight(self.grams - other.grams)

    def __neg__(self) -> Weight:
        return Weight(-self.grams)

    def __mul__(self, factor: Decimal | int | str) -> Weight:
        return Weight(self.grams * to_decimal(factor))

    __rmul__ = __mul__

    def rounded(self) -> Weight:
        return Weight(round_weight(self.grams))

    def __str__(self):
        return f"{self.grams} g"
