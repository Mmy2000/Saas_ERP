from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.inventory.models import Item, ItemStatus
from apps.inventory.tests.test_transfers_stocktakes import Stock
from apps.ledger.selectors import party_balance, trial_balance
from apps.parties.models import Party
from apps.parties.services import PartyData, create_customer
from apps.purchasing.returns import (
    ReturnLineInput,
    SupplierReturnInput,
    post_supplier_return,
    void_supplier_return,
)
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def stock(tenant_a):
    with tenant_context(tenant_a.id):
        stock = Stock()
        stock.supplier = Party.objects.get(name="Factory")
        yield stock


def send_back(stock, *lines, supplier=None):
    return post_supplier_return(SupplierReturnInput(
        branch_id=stock.branch.pk, supplier_id=(supplier or stock.supplier).pk, lines=lines))


def test_pieces_and_weight_go_back(stock):
    before = party_balance(stock.supplier)
    # Rings: 11 g of 18K → 8.25 fine g and 1,650 making; chain: 20 g of 21K, 2,000 making.
    assert before == {"XAU": Decimal("-25.75"), "EGP": Decimal("-3650")}
    doc = send_back(stock, ReturnLineInput(barcode=stock.ring_a.barcode),
                    ReturnLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                                    gross_weight_g="5", qty=1))
    assert doc.number == "01-PR-2026-000001"
    assert (doc.total_qty, doc.total_gross_weight_g) == (2, Decimal("10.000"))
    stock.ring_a.refresh_from_db()
    assert stock.ring_a.status == ItemStatus.RETURNED_TO_SUPPLIER
    assert stock.lot(stock.branch).gross_weight_g == Decimal("15.000")
    # Ring A: 3.75 fine g and 750 making; 5 g of chain: 4.375 fine g and 500 making.
    assert party_balance(stock.supplier) == {"XAU": Decimal("-17.625"), "EGP": Decimal("-2400")}
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit

    void_supplier_return(doc.pk)
    doc.refresh_from_db()
    assert doc.status == DocStatus.VOIDED
    assert Item.objects.get(pk=stock.ring_a.pk).status == ItemStatus.IN_STOCK
    assert stock.lot(stock.branch).gross_weight_g == Decimal("20.000")
    assert party_balance(stock.supplier) == before


def test_rules(stock):
    with pytest.raises(ValidationError):  # a customer is not a supplier
        send_back(stock, ReturnLineInput(barcode=stock.ring_a.barcode),
                  supplier=create_customer(PartyData(name="Mona")))
    with pytest.raises(ValidationError):
        send_back(stock, ReturnLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                                         gross_weight_g="25"))
    with pytest.raises(ValidationError):
        send_back(stock, ReturnLineInput(barcode=stock.ring_a.barcode),
                  ReturnLineInput(barcode=stock.ring_a.barcode))


def test_api_and_pages(tenant_a, stock):
    client = login(tenant_a)
    made = client.post("/api/v1/purchasing/returns/", {
        "branch": stock.branch.pk, "supplier": stock.supplier.pk,
        "lines": [{"barcode": stock.ring_b.barcode}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="pr1")
    assert made.status_code == 201, made.content
    for path in ("/purchasing/returns/", "/purchasing/returns/new/",
                 f"/purchasing/returns/{made.json()['id']}/",
                 f"/stock/items/{stock.ring_b.pk}/"):
        assert client.get(path).status_code == 200, path
    voided = client.post(f"/api/v1/purchasing/returns/{made.json()['id']}/void/", {},
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="pr1v")
    assert voided.json()["status"] == "voided"
