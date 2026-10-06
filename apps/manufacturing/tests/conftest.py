import pytest

from apps.core.tenancy import tenant_context
from apps.inventory.tests.test_transfers_stocktakes import Stock
from apps.parties.services import PartyData, create_workshop


@pytest.fixture
def stock(tenant_a):
    """A branch with stock (the chain lot holds 20 g of 21K, making cost 100/g) and a workshop."""
    with tenant_context(tenant_a.id):
        stock = Stock()
        stock.workshop = create_workshop(PartyData(name="Atef Workshop"))
        stock.rings = stock.ring_a.category
        yield stock
