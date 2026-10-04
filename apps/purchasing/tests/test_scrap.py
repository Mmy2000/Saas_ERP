from decimal import Decimal

import pytest
from django.db import connection

from apps.core.errors import ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.inventory.models import LotBalance
from apps.ledger.selectors import party_balance, trial_balance
from apps.parties.models import Party
from apps.parties.services import PartyData, create_customer
from apps.purchasing.models import ScrapPurchase, ScrapSale
from apps.purchasing.scrap import ScrapInput, ScrapLineInput, buy_scrap, sell_scrap, void_scrap
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from apps.treasury.holders import balance, default_cash_box
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.supplier = Party.objects.get(name="Factory")
        post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))
        yield shop


def scrap_g(shop) -> Decimal:
    lot = LotBalance.objects.filter(lot__category__code="SCRAP", lot__karat=shop.k21).first()
    return lot.gross_weight_g if lot else Decimal(0)


def drawer(shop) -> Decimal:
    return balance(default_cash_box(shop.branch, shop.bank.currency).account_id)[0]


def buy(shop, weight="4", loss="0", **kw):
    kw.setdefault("seller_name", "Walk-in")
    kw.setdefault("payment", "cash")
    return buy_scrap(ScrapInput(branch_id=shop.branch.pk, lines=(
        ScrapLineInput(karat_id=shop.k21.pk, gross_weight_g=weight, loss_weight_g=loss),), **kw))


def sell(shop, weight, price="4100", **kw):
    kw.setdefault("payment", "account")
    return sell_scrap(ScrapInput(branch_id=shop.branch.pk, party_id=shop.supplier.pk, lines=(
        ScrapLineInput(karat_id=shop.k21.pk, gross_weight_g=weight, price=price),), **kw))


class TestBuy:
    def test_walk_in_paid_in_cash(self, shop):
        purchase = buy(shop, seller_phone="01012345678", seller_id_number="2900-101 01234.56")
        assert purchase.number == "01-SB-2026-000001"
        assert purchase.total_amount == Decimal("15600.00")  # 4 g × 3900 scrap price
        assert purchase.total_fine_weight_g == Decimal("3.5000")
        assert scrap_g(shop) == Decimal("4.000")
        assert drawer(shop) == RING_A_TOTAL - Decimal("15600")
        assert purchase.seller_id_number == "29001010123456"
        with connection.cursor() as cursor:
            cursor.execute("SELECT seller_id_encrypted FROM purchasing_scrappurchase WHERE id = %s",
                           [purchase.pk])
            assert "2900" not in cursor.fetchone()[0]
        tb = trial_balance()
        assert tb.total_debit == tb.total_credit

    def test_rules(self, shop):
        with pytest.raises(ValidationError):  # who is selling must be written down
            buy(shop, seller_name="")
        with pytest.raises(ValidationError):  # more than the drawer holds
            buy(shop, weight="10")
        with pytest.raises(ValidationError):
            buy(shop, loss="4")

    def test_on_the_customers_account(self, shop):
        customer = create_customer(PartyData(name="Mona"))
        buy(shop, weight="2", payment="account", party_id=customer.pk, seller_name="")
        assert party_balance(customer) == {"EGP": Decimal("-7800.00")}  # we owe her


class TestSell:
    def test_gain_on_the_cost(self, shop):
        buy(shop)  # 4 g at 3900
        sale = sell(shop, "3")  # 3 g at 4100
        assert (sale.total_amount, sale.total_cost, sale.gain) == (
            Decimal("12300.00"), Decimal("11700.00"), Decimal("600.00"))
        assert scrap_g(shop) == Decimal("1.000")
        assert party_balance(shop.supplier)["EGP"] == Decimal("-1650") + Decimal("12300")
        gain = next(r for r in trial_balance().rows if r.account.role == "metal_gain")
        assert gain.credit == Decimal("600")
        with pytest.raises(ValidationError):
            sell(shop, "2")  # only 1 g left

    def test_cancelling(self, shop):
        purchase = buy(shop)
        sale = sell(shop, "4", payment="cash")
        void_scrap(ScrapSale, sale.pk)
        assert scrap_g(shop) == Decimal("4.000")
        void_scrap(ScrapPurchase, purchase.pk)
        purchase.refresh_from_db()
        assert purchase.status == DocStatus.VOIDED and scrap_g(shop) == 0
        assert drawer(shop) == RING_A_TOTAL
        assert not any(r.account.role in ("metal_gain", "inventory_scrap")
                       for r in trial_balance().rows)


def test_api_and_pages(tenant_a, shop):
    client = login(tenant_a)
    quote = client.post("/api/v1/purchasing/scrap/quote/", {
        "branch": shop.branch.pk, "lines": [{"karat": shop.k21.pk, "gross_weight_g": "2"}]},
        content_type="application/json")
    assert quote.status_code == 200, quote.content
    assert quote.json()["total"] == "7800.00"
    bought = client.post("/api/v1/purchasing/scrap/purchases/", {
        "branch": shop.branch.pk, "payment": "cash", "seller_name": "Walk-in",
        "lines": [{"karat": shop.k21.pk, "gross_weight_g": "2"}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="sb1")
    assert bought.status_code == 201, bought.content
    sold = client.post("/api/v1/purchasing/scrap/sales/", {
        "branch": shop.branch.pk, "party": shop.supplier.pk, "payment": "account",
        "lines": [{"karat": shop.k21.pk, "gross_weight_g": "1", "price": "4000"}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="ss1")
    assert sold.status_code == 201, sold.content
    for path in ("/purchasing/scrap/", "/purchasing/scrap/?tab=sales",
                 "/purchasing/scrap/buy/", "/purchasing/scrap/sell/",
                 f"/purchasing/scrap/bought/{bought.json()['id']}/",
                 f"/purchasing/scrap/sold/{sold.json()['id']}/",
                 f"/print/scrap_purchase/{bought.json()['id']}/",
                 f"/print/scrap_sale/{sold.json()['id']}/"):
        assert client.get(path).status_code == 200, path
