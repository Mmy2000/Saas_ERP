"""Bulk lots: balances follow their movements and never go below zero."""

from datetime import date
from decimal import Decimal

import pytest

from apps.catalog.models import Karat
from apps.core.errors import DomainError
from apps.core.tenancy import tenant_context
from apps.inventory.models import LotBalance, MovementType
from apps.inventory.services import DocRef, lot_for, move_lot, scrap_category
from apps.org.models import Branch

pytestmark = pytest.mark.django_db
DAY = date(2026, 9, 24)


@pytest.fixture
def lot(tenant_a):
    with tenant_context(tenant_a.id):
        yield lot_for(scrap_category(), Karat.objects.get(code=21), Branch.objects.get(code=1))


def take(lot, grams, cost="0"):
    return move_lot(lot, qty=0, gross_weight_g=f"-{grams}", cost_amount=f"-{cost}",
                    movement_type=MovementType.SCRAP_OUT, business_date=DAY, doc=DocRef("t", 1))


def test_the_last_grams_take_the_fine_weight_that_is_left(lot):
    """Each movement's fine weight is rounded on its own: 3.003 g at 875‰ is 2.6276 fine g, but
    three times 1.001 g is 3 × 0.8759 = 2.6277. The last one takes what is left (0.8758), so
    the lot empties exactly instead of breaking its non-negative rule (a 500 on a real sale)."""
    move_lot(lot, qty=0, gross_weight_g="3.003", cost_amount="900",
             movement_type=MovementType.SCRAP_IN, business_date=DAY, doc=DocRef("t", 1))
    taken = [take(lot, "1.001", "300").fine_weight_g for _ in range(3)]
    assert taken == [Decimal("-0.8759"), Decimal("-0.8759"), Decimal("-0.8758")]
    balance = LotBalance.objects.get(lot=lot)
    assert (balance.gross_weight_g, balance.fine_weight_g, balance.cost_amount) == (0, 0, 0)


def test_a_lot_never_goes_below_zero(lot):
    move_lot(lot, qty=0, gross_weight_g="2", cost_amount="0",
             movement_type=MovementType.SCRAP_IN, business_date=DAY, doc=DocRef("t", 1))
    with pytest.raises(DomainError):
        take(lot, "2.001")
