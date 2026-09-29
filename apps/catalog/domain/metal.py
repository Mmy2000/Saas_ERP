"""Karat, fineness and weight conversions (§8.1–8.3, ADR-009).

This is the only place karat conversion factors may appear. The legacy system repeated them
~200 times with three different answers (24k as 999.9/875 or 8/7; 22k as 22/21 or, wrongly,
22/24). Here the factor is always derived from the fineness, which is tenant data (Karat
model, seeded from DEFAULT_GOLD_FINENESS).

Metal balances are stored in fine grams. "21k-equivalent" is a display unit only.
"""

from __future__ import annotations

from decimal import Decimal

from apps.core.numeric import FINE_WEIGHT, MONEY, quantize, to_decimal

PER_MILLE = Decimal("1000")

#: Default fineness (‰) per gold karat. A tenant may choose 1000 for 24k (OQ-8).
DEFAULT_GOLD_FINENESS: dict[int, Decimal] = {
    24: Decimal("999.9"),
    22: Decimal("916.667"),
    21: Decimal("875"),
    18: Decimal("750"),
    14: Decimal("583.333"),
}
DEFAULT_SILVER_FINENESS = Decimal("925")

#: Egyptian convention: gold balances are shown as 21k-equivalent grams.
DEFAULT_REFERENCE_FINENESS = DEFAULT_GOLD_FINENESS[21]


def _fineness(value: Decimal | int | str) -> Decimal:
    fineness = to_decimal(value)
    if not (Decimal(0) < fineness <= PER_MILLE):
        raise ValueError(f"Fineness must be in (0, 1000] ‰, got {fineness}")
    return fineness


def fine_weight(metal_weight_g: Decimal | int | str, fineness: Decimal | int | str) -> Decimal:
    """Pure-metal grams in `metal_weight_g` of the given fineness, rounded to 4 dp."""
    return quantize(to_decimal(metal_weight_g) * _fineness(fineness) / PER_MILLE, FINE_WEIGHT)


def equivalent_weight(
    metal_weight_g: Decimal | int | str,
    fineness: Decimal | int | str,
    reference_fineness: Decimal | int | str = DEFAULT_REFERENCE_FINENESS,
) -> Decimal:
    """Grams of reference-karat metal holding the same fine gold (e.g. 21k-equivalent).

    Computed from the unrounded fine weight, so the result is not rounded twice (§8.6).
    """
    weight = to_decimal(metal_weight_g)
    return quantize(weight * _fineness(fineness) / _fineness(reference_fineness), FINE_WEIGHT)


def weight_from_fine(
    fine_weight_g: Decimal | int | str, target_fineness: Decimal | int | str
) -> Decimal:
    """Grams of metal at `target_fineness` that contain `fine_weight_g` of pure metal."""
    return quantize(to_decimal(fine_weight_g) * PER_MILLE / _fineness(target_fineness),
                    FINE_WEIGHT)


def derive_price_per_gram(
    reference_price_per_g: Decimal | int | str,
    reference_fineness: Decimal | int | str,
    target_fineness: Decimal | int | str,
) -> Decimal:
    """Price per gram of another karat, derived from the reference karat's board price by the
    fineness ratio and rounded to 0.01 (§8.3). Boards store the derived prices explicitly."""
    price = to_decimal(reference_price_per_g)
    return quantize(price * _fineness(target_fineness) / _fineness(reference_fineness), MONEY)
