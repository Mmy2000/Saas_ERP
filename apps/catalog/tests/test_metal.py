from decimal import Decimal

import pytest

from apps.catalog.domain.metal import (
    DEFAULT_GOLD_FINENESS as F,
)
from apps.catalog.domain.metal import (
    derive_price_per_gram,
    equivalent_weight,
    fine_weight,
    weight_from_fine,
)


def test_fine_weight_21k():
    assert fine_weight("10", F[21]) == Decimal("8.7500")


def test_24k_to_21k_equivalent_uses_999_9_by_default():
    # Legacy `999.9/875` (the majority formula), not `8/7`.
    assert equivalent_weight("100", F[24]) == Decimal("114.2743")


def test_24k_at_1000_matches_legacy_8_over_7():
    assert equivalent_weight("7", "1000") == Decimal("8.0000")


def test_22k_to_21k_is_22_over_21_not_22_over_24():
    # Legacy GHS13/GHSS12a used 22/24, a bug (§2.5).
    result = equivalent_weight("21", F[22])
    assert result == Decimal("22.0000")
    assert result != (Decimal("21") * 22 / 24).quantize(Decimal("0.0001"))


def test_18k_to_21k_is_six_sevenths():
    assert equivalent_weight("7", F[18]) == Decimal("6.0000")


def test_equivalent_is_not_double_rounded():
    # 0.01 g of 14k: via a rounded fine weight (0.0058) it would come out as 0.0066.
    assert equivalent_weight("0.01", F[14]) == Decimal("0.0067")


def test_weight_from_fine_round_trip():
    fine = fine_weight("12.34", F[18])
    assert fine == Decimal("9.2550")
    assert weight_from_fine(fine, F[18]) == Decimal("12.3400")


def test_derive_price_from_21k_board_price():
    assert derive_price_per_gram("4000", F[21], F[18]) == Decimal("3428.57")
    assert derive_price_per_gram("4000", F[21], F[24]) == Decimal("4570.97")


@pytest.mark.parametrize("bad", ["0", "-1", "1000.1"])
def test_rejects_impossible_fineness(bad):
    with pytest.raises(ValueError):
        fine_weight("1", bad)
