"""Bullion and sealed coins: bought into stock at the buy price, and ordered by weight on a
reservation before they are in stock, then delivered when it is completed."""

from decimal import Decimal

import pytest

from apps.catalog.models import Karat, ProductFamily, Tracking
from apps.catalog.services import (
    CreateItemCategoryCommand,
    MakingChargeInput,
    create_item_category,
)
from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.inventory.models import LotBalance
from apps.ledger.selectors import trial_balance
from apps.parties.services import PartyData, create_customer
from apps.purchasing.models import ScrapPurchase
from apps.purchasing.scrap import ScrapInput, ScrapLineInput, buy_scrap, void_scrap
from apps.sales.reservations import (
    OrderInput,
    ReservationInput,
    complete_reservation,
    create_reservation,
)
from apps.sales.tests.test_sales import Shop
from conftest import login

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()  # 21K at 4000 / 3950 / 3900; 24K (999.9) derived from it
        shop.k24 = Karat.objects.get(code=24, metal__code="gold")
        shop.bars = create_item_category(CreateItemCategoryCommand(
            code="BAR", name="Gold bars", product_family=ProductFamily.BULLION_COIN,
            tracking=Tracking.BULK, default_karat_id=shop.k24.pk,
            making_charges=(MakingChargeInput("EGP", list_rate_per_g="20"),)))
        yield shop


def _board_price(shop, side: str) -> D:
    from apps.pricing.selectors import current_price_board

    return current_price_board().price(shop.k24, side)


def _buy(shop, grams="10", qty=1, **kw):
    return buy_scrap(ScrapInput(branch_id=shop.branch.pk, payment="bank_transfer",
                                bank_account_id=shop.bank.pk, seller_name="Walk-in",
                                lines=(ScrapLineInput(karat_id=shop.k24.pk, gross_weight_g=grams,
                                                      category_id=shop.bars.pk, qty=qty, **kw),)))


def test_buying_bullion_into_stock(tenant_a, shop):
    with tenant_context(tenant_a.id):
        purchase = _buy(shop)
        line = purchase.lines.get()
        assert (line.category, line.qty, line.loss_weight_g) == (shop.bars, 1, 0)
        buy = _board_price(shop, "buy")
        assert line.price_per_g == buy and line.amount == (buy * 10).quantize(D("0.01"))
        lot = LotBalance.objects.get(lot__category=shop.bars)
        assert (lot.gross_weight_g, lot.qty) == (D("10.000"), 1)
        tb = trial_balance()
        assert tb.total_debit == tb.total_credit
        gold = next(r for r in tb.rows if r.account.role == "inventory_gold")
        assert gold.metals.get("gold") or gold.debit  # on gold stock, not scrap

        void_scrap(ScrapPurchase, purchase.pk)
        lot.refresh_from_db()
        assert (lot.gross_weight_g, lot.qty) == (0, 0)
        with pytest.raises(ValidationError):
            buy_scrap(ScrapInput(branch_id=shop.branch.pk, payment="cash", seller_name="x",
                                 lines=(ScrapLineInput(karat_id=shop.k24.pk, gross_weight_g="5",
                                                       category_id=shop.ring_a.category_id),)))


def test_ordering_bullion_before_it_is_in_stock(tenant_a, shop):
    with tenant_context(tenant_a.id):
        customer = create_customer(PartyData(name="Mona"))
        reservation = create_reservation(ReservationInput(
            branch_id=shop.branch.pk, customer_id=customer.pk, lines=(),
            orders=(OrderInput(category_id=shop.bars.pk, karat_id=shop.k24.pk,
                               gross_weight_g="10", qty=1),), price_locked=True))
        line = reservation.lines.get()
        assert line.item is None and line.category == shop.bars
        total = ((_board_price(shop, "sell") + 20) * 10).quantize(D("0.01"))
        assert reservation.quoted_total == total  # 24K sell + the bars' 20/g making, × 10 g
        with pytest.raises(ValidationError):  # not in stock yet: an error on the line
            complete_reservation(reservation.pk, payments=[shop.cash(str(total))])

        from django.utils import translation

        from apps.iam.authz import build_actor
        from apps.iam.models import Membership
        from apps.org.dashboard import needs_attention

        actor = build_actor(Membership.objects.get(username="owner"))
        with translation.override("en"):
            assert not any("Ordered gold" in i.label for i in needs_attention(actor))
            _buy(shop)  # the bar arrives
            assert any("Ordered gold" in i.label for i in needs_attention(actor))
        invoice = complete_reservation(reservation.pk, payments=[shop.cash(str(total))])
        sold = invoice.lines.get()
        assert (sold.lot.category, sold.gross_weight_g, sold.line_total) == (
            shop.bars, D("10.000"), total)
        assert LotBalance.objects.get(lot__category=shop.bars).gross_weight_g == 0


def test_reservation_pages_and_orders_quote(tenant_a, shop):
    client = login(tenant_a, language="en")
    assert b"Order by weight" in client.get("/sales/reservations/new/").content
    quote = client.post("/api/v1/sales/reservations/orders-quote/", {
        "branch": shop.branch.pk, "orders": [{"category": shop.bars.pk, "karat": shop.k24.pk,
                                              "gross_weight_g": "5", "qty": 1}]},
        content_type="application/json").json()
    with tenant_context(tenant_a.id):
        expected = ((_board_price(shop, "sell") + 20) * 5).quantize(D("0.01"))
    assert D(quote["total"]) == expected
    with tenant_context(tenant_a.id):
        customer = create_customer(PartyData(name="Ali"))
    made = client.post("/api/v1/sales/reservations/", {
        "branch": shop.branch.pk, "customer": customer.pk, "lines": [],
        "orders": [{"category": shop.bars.pk, "karat": shop.k24.pk, "gross_weight_g": "5",
                    "qty": 1}]}, content_type="application/json", HTTP_IDEMPOTENCY_KEY="r1")
    assert made.status_code == 201, made.content
    page = client.get(f"/sales/reservations/{made.json()['id']}/").content.decode()
    assert "Ordered" in page and "Not in stock yet" in page
    printed = client.get(f"/print/reservation/{made.json()['id']}/")
    assert printed.status_code == 200 and b"Gold bars" in printed.content
    buy_page = client.get("/purchasing/scrap/buy/")
    assert buy_page.status_code == 200 and b"Gold bars" in buy_page.content
