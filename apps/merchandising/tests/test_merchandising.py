"""Price changes (bulk, one piece, undo) and offers (making discounts, cashback) on sales."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.errors import DomainError
from apps.core.tenancy import tenant_context
from apps.inventory.models import Item
from apps.ledger.models import Account
from apps.ledger.selectors import party_balance
from apps.merchandising import prices
from apps.merchandising.models import Offer
from apps.merchandising.offers import save_offer
from apps.parties.services import PartyData, create_customer
from apps.sales.returns import create_return
from apps.sales.services import post_sale, quote_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from conftest import login

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()  # rings: 5 g and 6 g of 18K, making 250/g (cost 150/g); 18K at 3428.57


def _offer(**values):
    today = timezone.localdate()
    data = {"name": "Eid", "kind": "making_percent", "value": D("40"), "starts_on": today,
            "ends_on": today + timedelta(days=3), **values}
    return save_offer(data)


def test_bulk_price_change_and_undo(tenant_a, shop):
    with tenant_context(tenant_a.id):
        rules = prices.PieceFilter(karat_ids=[shop.k18.pk])
        preview = prices.preview("making", "add", "10", rules)
        assert preview["count"] == 2 and preview["sample"][0]["old"] == "250.0000"
        assert preview["sample"][0]["new"] == "260.0000"
        change = prices.apply_change("making", "percent", "-10", rules, summary="18K")
        assert change.item_count == 2
        assert {i.list_making_rate for i in Item.objects.all()} == {D("225.0000")}

        # one piece changed again since: undo leaves it alone
        prices.set_piece_price(shop.ring_b.pk, "making", "300")
        undone = prices.undo_change(change.pk)
        assert undone.undone_count == 1
        shop.ring_a.refresh_from_db()
        shop.ring_b.refresh_from_db()
        assert (shop.ring_a.list_making_rate, shop.ring_b.list_making_rate) == (
            D("250.0000"), D("300.0000"))
        with pytest.raises(DomainError):
            prices.undo_change(change.pk)
        with pytest.raises(DomainError):
            prices.apply_change("making", "set", "100", prices.PieceFilter(barcodes=["nope"]))


def test_an_offer_lowers_the_making_charge_but_never_below_cost(tenant_a, shop):
    with tenant_context(tenant_a.id):
        _offer(category_ids=[shop.ring_a.category_id])
        quote = quote_sale(shop.sale(shop.line(shop.ring_a)))
        line = quote.lines[0]
        # 250 × 60% = 150/g: exactly the cost, so allowed; saving 100/g × 5 g
        assert (line.making_rate, line.offer_name) == (D("150.0000"), "Eid")
        assert line.offer_saving == D("500.00") and quote.offer_saving == D("500.00")
        assert line.line_total == RING_A_TOTAL - D("500.00")

        Offer.objects.update(value=D("80"))  # 50/g asked: held at the 150/g cost
        line = quote_sale(shop.sale(shop.line(shop.ring_a))).lines[0]
        assert line.making_rate_net == D("150.0000") and line.at_cost_floor
        assert line.offer_saving == D("500.00")

        Offer.objects.update(starts_on=timezone.localdate() + timedelta(days=1))
        assert quote_sale(shop.sale(shop.line(shop.ring_a))).lines[0].offer_name == ""


def test_cashback_goes_to_the_customer_and_comes_back_on_return(tenant_a, shop):
    with tenant_context(tenant_a.id):
        _offer(kind="cashback_amount", value=D("200"), name="Gift")
        customer = create_customer(PartyData(name="Mona"))
        walk_in = quote_sale(shop.sale(shop.line(shop.ring_a)))
        assert walk_in.lines[0].cashback == D("200.00") and walk_in.cashback == 0

        invoice = post_sale(shop.sale(shop.line(shop.ring_a), customer_id=customer.pk,
                                      payments=[shop.cash(RING_A_TOTAL)]))
        line = invoice.lines.get()
        assert line.cashback_amount == D("200.00") and line.cashback_offer.name == "Gift"
        customers = Account.objects.get(role="customers")
        assert party_balance(customer, customers) == {"EGP": D("-200.00")}  # credit to use
        promotions = Account.objects.get(role="promotions")
        assert sum(line.quantity for line in promotions.lines.all()) == D("200.00")

        create_return(invoice.pk, [line.pk], refund_method="cash")
        assert party_balance(customer, customers) == {}


def test_the_offers_feature_switch(tenant_a, shop):
    from apps.platform.tenants import features
    from apps.platform.tenants.models import TenantFeature

    with tenant_context(tenant_a.id):
        _offer()
    TenantFeature.objects.create(tenant=tenant_a, key="offers", enabled=False)
    features.forget(tenant_a.id)
    with tenant_context(tenant_a.id):
        assert quote_sale(shop.sale(shop.line(shop.ring_a))).lines[0].offer_name == ""


def test_pages_and_api(tenant_a, shop, make_member):
    client = login(tenant_a, language="en")
    today = timezone.localdate()
    created = client.post("/api/v1/merchandising/offers/", {
        "name": "Summer", "kind": "making_rate", "value": "200", "starts_on": str(today),
        "ends_on": str(today + timedelta(days=7)), "karat_ids": [shop.k18.pk],
        "barcodes": ""}, content_type="application/json", HTTP_IDEMPOTENCY_KEY="o1")
    assert created.status_code == 201, created.content
    page = client.get(created.json()["url"]).content.decode()
    assert "Summer" in page and "Pieces covered now" in page
    assert "Summer" in client.get("/sales/offers/").content.decode()
    bad = client.put(f"/api/v1/merchandising/offers/{created.json()['id']}/", {
        "name": "Summer", "kind": "making_percent", "value": "150", "starts_on": str(today),
        "ends_on": str(today)}, content_type="application/json")
    assert bad.status_code == 400  # more than 100%

    body = {"target": "making", "method": "set", "value": "275",
            "filters": {"karat_ids": [shop.k18.pk], "barcodes": shop.ring_a.barcode}}
    preview = client.post("/api/v1/merchandising/prices/preview/", body,
                          content_type="application/json", HTTP_IDEMPOTENCY_KEY="p0").json()
    assert preview["count"] == 1
    applied = client.post("/api/v1/merchandising/prices/", body,
                          content_type="application/json", HTTP_IDEMPOTENCY_KEY="p1")
    assert applied.status_code == 201
    assert b"275.00" in client.get(applied.json()["url"]).content
    item_page = client.get(f"/stock/items/{shop.ring_a.pk}/").content.decode()
    assert "Selling price" in item_page and "On offer today" in item_page
    assert client.get("/stock/prices/").status_code == 200

    make_member(tenant_a, "viewer", "viewer")
    viewer = login(tenant_a, username="viewer")
    assert viewer.get("/sales/offers/").status_code == 200
    assert viewer.get("/stock/prices/").status_code == 403
    assert viewer.post("/api/v1/merchandising/prices/", body, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY="p2").status_code == 403
