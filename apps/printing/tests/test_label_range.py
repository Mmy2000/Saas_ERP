"""Labels for a barcode range or for everything the stock list shows (legacy GHD29, GHD50)."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
import pytest

from apps.core.tenancy import tenant_context
from apps.inventory.models import Item
from apps.inventory.selectors import filter_pieces
from apps.printing import views as printing_views
from apps.printing.tests.test_printing import shop  # noqa: F401
from conftest import login

pytestmark = pytest.mark.django_db


def test_range_filter(shop):
    first, second = sorted([shop.ring_a.barcode, shop.ring_b.barcode])
    found, filters = filter_pieces(Item.objects.all(), {"from_code": first, "to_code": first})
    assert [i.barcode for i in found] == [first] and filters["status"] == "in_stock"
    found, _f = filter_pieces(Item.objects.all(), {"from_code": first})
    assert {i.barcode for i in found} == {first, second}
    found, _f = filter_pieces(Item.objects.all(), {"to_code": "1"})  # other lengths left out
    assert not found.exists()


def test_label_pages(tenant_a, shop, monkeypatch):
    first, second = sorted([shop.ring_a.barcode, shop.ring_b.barcode])
    client = login(tenant_a, language="en")
    page = client.get("/stock/labels/", {"stock": "1", "from_code": second}).content.decode()
    assert second in page and first not in page and "1 label" in page
    stock = client.get("/stock/", {"from_code": first}).content.decode()
    assert "Labels for all 2 pieces shown" in stock and "stock=1" in stock
    monkeypatch.setattr(printing_views, "MAX_LABELS", 1)
    capped = client.get("/stock/labels/", {"stock": "1"}).content.decode()
    assert "Only the first 1 pieces are printed" in capped
    zpl = client.get("/stock/labels/zpl/", {"stock": "1", "to_code": first})
    assert zpl.status_code == 200 and first.encode() in zpl.content
    with tenant_context(tenant_a.id):
        assert Item.objects.count() >= 2
