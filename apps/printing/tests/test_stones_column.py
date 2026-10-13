"""Diamond pieces print each stone on the sales invoice (legacy GHD15 / GHZ17); gold-only
invoices keep their usual columns."""

from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context
from apps.diamonds.models import ItemStone
from apps.printing.documents import stones_text
from conftest import login

pytestmark = pytest.mark.django_db


def _sell(tenant, stones=()):
    from apps.sales.services import post_sale
    from apps.sales.tests.test_sales import RING_A_TOTAL, Shop

    with tenant_context(tenant.id):
        shop = Shop()
        for stone in stones:
            ItemStone.objects.create(item=shop.ring_a, **stone)
        text = stones_text(shop.ring_a)
        sale = post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
        return sale, text


def test_stones_print_only_when_there_are_any(tenant_a):
    sale, text = _sell(tenant_a, [
        {"count": 1, "carat": Decimal("0.500"), "color": "D", "clarity": "VS1",
         "cut": "Excellent", "lab": "GIA", "certificate_no": "2141438171"},
        {"count": 12, "carat": Decimal("0.240")}])
    assert text == "1 × 0.5 ct D VS1 Excellent · GIA 2141438171 + 12 × 0.24 ct"
    client = login(tenant_a, language="en")
    html = client.get(f"/sales/{sale.pk}/print/").content.decode()
    assert "Stones" in html and "GIA 2141438171" in html


def test_gold_invoice_has_no_stones_column(tenant_a):
    sale, text = _sell(tenant_a)
    assert text == ""
    client = login(tenant_a, language="en")
    assert "Stones" not in client.get(f"/sales/{sale.pk}/print/").content.decode()
