"""Selling bulk gold by weight (chain by the gram, bullion) out of a lot."""

from datetime import date
from decimal import Decimal

import pytest

from apps.catalog.models import CategoryMakingCharge, Currency, ProductFamily, Tracking
from apps.catalog.services import CreateItemCategoryCommand, create_item_category
from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.inventory.models import LotBalance
from apps.inventory.services import scrap_category
from apps.ledger.selectors import trial_balance
from apps.purchasing.services import InvoiceInput, LineInput, post_invoice, save_draft
from apps.sales.returns import create_return, void_return
from apps.sales.services import SaleLineInput, TradeInInput, post_sale, quote_sale, void_sale
from apps.sales.tests.test_sales import Shop
from conftest import login

pytestmark = pytest.mark.django_db


class Chain(Shop):
    """Shop plus 20 g (4 pieces) of 21K chain bought at 100/g making, sold at 180/g."""

    def __init__(self):
        super().__init__()
        self.chain = create_item_category(CreateItemCategoryCommand(
            code="2020", name="Chain", product_family=ProductFamily.GOLD,
            tracking=Tracking.BULK))
        CategoryMakingCharge.objects.create(category=self.chain,
                                            currency=Currency.objects.get(code="EGP"),
                                            list_rate_per_g=Decimal("180"))
        post_invoice(save_draft(InvoiceInput(
            supplier_id=self.ring_a.supplier_id, branch_id=self.branch.pk,
            business_date=date(2026, 9, 1), currency_code="EGP",
            lines=(LineInput(category_id=self.chain.pk, karat_id=self.k21.pk, qty=4,
                             gross_weight_g="20", making_cost_rate="100"),))).pk)

    def grams(self, weight, qty=0, discount="0"):
        return SaleLineInput(category_id=self.chain.pk, karat_id=self.k21.pk,
                             gross_weight_g=weight, qty=qty, discount_rate=discount)

    def lot(self):
        return LotBalance.objects.get(lot__category=self.chain)


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Chain()


def test_priced_like_a_piece(shop):
    quote = quote_sale(shop.sale(shop.grams("5")))
    [line] = quote.lines
    # 21K sells at 4000/g; making 180/g → (4000 + 180) × 5.
    assert line.line_total == Decimal("20900.00") and line.making_rate == 180
    assert line.cost_amount == Decimal("500.00")  # the lot's 100/g
    floored = quote_sale(shop.sale(shop.grams("5", discount="1"))).lines[0]
    assert floored.making_rate_net == 100 and floored.at_cost_floor


def test_sale_return_and_cancel(shop):
    invoice = post_sale(shop.sale(shop.grams("5", qty=1), payments=[shop.cash("20900")]))
    line = invoice.lines.get()
    assert (line.lot_id, line.item_id, line.qty, line.category) == (
        shop.lot().lot_id, None, 1, shop.chain)
    assert line.fine_weight_g == Decimal("4.3750")
    assert (shop.lot().gross_weight_g, shop.lot().qty) == (Decimal("15.000"), 3)
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit

    sales_return = create_return(invoice.pk, [line.pk], refund_method="cash")
    assert shop.lot().gross_weight_g == Decimal("20.000")
    void_return(sales_return.pk)
    assert shop.lot().gross_weight_g == Decimal("15.000")

    second = post_sale(shop.sale(shop.grams("15", qty=3), payments=[shop.cash("62700")]))
    assert shop.lot().gross_weight_g == 0
    void_sale(second.pk)
    assert (shop.lot().gross_weight_g, shop.lot().qty) == (Decimal("15.000"), 3)


def test_cannot_sell_more_than_the_lot(shop):
    with pytest.raises(ValidationError) as exc:  # two lines, 21 g in all
        quote_sale(shop.sale(shop.grams("12"), shop.grams("9")))
    assert "1" in exc.value.fields["lines"]
    post_sale(shop.sale(shop.line(shop.ring_a), trade_ins=[  # puts 21K scrap in stock
        TradeInInput(karat_id=shop.k21.pk, gross_weight_g="10")]))
    with pytest.raises(ValidationError):  # but scrap is sold from its own screen
        quote_sale(shop.sale(SaleLineInput(category_id=scrap_category().pk,
                                           karat_id=shop.k21.pk, gross_weight_g="1")))


def test_api_and_receipt(tenant_a, shop):
    client = login(tenant_a)
    body = {"branch": shop.branch.pk, "lines": [
        {"category": shop.chain.pk, "karat": shop.k21.pk, "gross_weight_g": "2.5", "qty": 0}],
        "payments": [{"kind": "cash", "currency": "EGP", "amount": "10450"}]}
    quote = client.post("/api/v1/sales/quote/", body, content_type="application/json")
    assert quote.json()["lines"][0]["line_total"] == "10450.00"
    sold = client.post("/api/v1/sales/invoices/", body, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY="bulk1")
    assert sold.status_code == 201, sold.content
    assert sold.json()["lines"][0]["lot"] and sold.json()["lines"][0]["barcode"] == ""
    page = client.get(f"/sales/{sold.json()['id']}/")
    assert page.status_code == 200 and b"Chain" in page.content
    assert client.get("/sales/new/").status_code == 200
