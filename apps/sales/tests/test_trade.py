from decimal import Decimal

import pytest

from apps.catalog.models import CategoryMakingCharge, Currency
from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.inventory.models import Item, ItemStatus
from apps.inventory.tests.test_transfers_stocktakes import Stock
from apps.ledger.selectors import party_balance, trial_balance
from apps.parties.services import PartyData, create_customer, create_trade_account
from apps.sales.models import SettlementBasis, TradeSaleLine
from apps.sales.trade import (
    TradeLineInput,
    TradeSaleInput,
    post_trade_return,
    post_trade_sale,
    quote_trade_sale,
    void_trade_return,
    void_trade_sale,
)
from apps.settlements.services import SettlementInput, post_settlement
from conftest import login

pytestmark = pytest.mark.django_db

# Ring A: 5 g of 18K (3.75 fine g, making cost 750). Chain lot: 20 g of 21K, making cost 100/g.
# 18K sells at 3,428.57/g and 21K at 4,000/g.


@pytest.fixture
def stock(tenant_a):
    with tenant_context(tenant_a.id):
        stock = Stock()
        stock.trader = create_trade_account(PartyData(name="Nour Jewellers"))
        yield stock


def sell(stock, *lines, basis=SettlementBasis.METAL, trader=None):
    return post_trade_sale(TradeSaleInput(
        branch_id=stock.branch.pk, trade_account_id=(trader or stock.trader).pk,
        settlement_basis=basis, lines=lines))


def chain(stock, grams="5", rate="30", qty=1):
    return TradeLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                          gross_weight_g=grams, qty=qty, making_rate=rate)


def balanced():
    tb = trial_balance()
    return tb.total_debit == tb.total_credit


def test_settled_in_gold(stock):
    doc = sell(stock, TradeLineInput(barcode=stock.ring_a.barcode, making_rate="50"), chain(stock))
    assert doc.number == "01-TS-2026-000001" and doc.status == DocStatus.POSTED
    assert (doc.total_qty, doc.total_gross_weight_g) == (2, Decimal("10.000"))
    assert doc.total_fine_weight_g == Decimal("8.125")
    assert (doc.making_amount, doc.money_amount) == (Decimal("400.00"), Decimal("400.00"))
    stock.ring_a.refresh_from_db()
    assert stock.ring_a.status == ItemStatus.SOLD
    assert stock.lot(stock.branch).gross_weight_g == Decimal("15.000")
    # The trader owes the fine gold itself plus the making charge in money.
    assert party_balance(stock.trader) == {"XAU": Decimal("8.125"), "EGP": Decimal("400")}
    roles = {r.account.role: r for r in trial_balance().rows}
    assert "sales_gold" not in roles  # no gold revenue: the gold is still owed in kind
    assert roles["sales_making"].credit == Decimal("400")
    assert balanced()

    # Take the chain back: the trader owes that gold and making charge no more.
    chain_line = doc.lines.get(lot__isnull=False)
    back = post_trade_return(doc.pk, [chain_line.pk])
    assert back.number == "01-TR-2026-000001" and back.money_amount == Decimal("150.00")
    assert stock.lot(stock.branch).gross_weight_g == Decimal("20.000")
    assert party_balance(stock.trader) == {"XAU": Decimal("3.75"), "EGP": Decimal("250")}
    with pytest.raises(DomainError):  # a line comes back once
        post_trade_return(doc.pk, [chain_line.pk])
    with pytest.raises(DomainError):  # returns are cancelled first
        void_trade_sale(doc.pk)

    void_trade_return(back.pk)
    assert stock.lot(stock.branch).gross_weight_g == Decimal("15.000")
    assert party_balance(stock.trader) == {"XAU": Decimal("8.125"), "EGP": Decimal("400")}
    again = post_trade_return(doc.pk, [chain_line.pk])  # returnable again once cancelled
    void_trade_return(again.pk)

    void_trade_sale(doc.pk)
    assert Item.objects.get(pk=stock.ring_a.pk).status == ItemStatus.IN_STOCK
    assert stock.lot(stock.branch).gross_weight_g == Decimal("20.000")
    assert party_balance(stock.trader) == {}
    assert balanced()


def test_settled_in_money(stock):
    doc = sell(stock, TradeLineInput(barcode=stock.ring_b.barcode, making_rate="50"),
               basis=SettlementBasis.MONEY)
    line = doc.lines.get()
    # 6 g × 3,428.57 = 20,571.42 of gold, 300 of making.
    assert (line.metal_amount, line.making_amount) == (Decimal("20571.42"), Decimal("300.00"))
    assert doc.money_amount == Decimal("20871.42")
    assert party_balance(stock.trader) == {"EGP": Decimal("20871.42")}
    roles = {r.account.role: r for r in trial_balance().rows}
    assert roles["sales_gold"].credit == Decimal("20571.42")
    assert roles["cogs_gold"].metals["XAU"] == Decimal("4.5")
    assert balanced()

    post_trade_return(doc.pk, [line.pk])
    assert party_balance(stock.trader) == {}
    assert Item.objects.get(pk=stock.ring_b.pk).status == ItemStatus.IN_STOCK
    assert balanced()


def test_making_rate_defaults_to_the_category_rate(stock):
    CategoryMakingCharge.objects.create(category=stock.chain,
                                        currency=Currency.objects.get(code="EGP"),
                                        list_rate_per_g="40")
    quote = quote_trade_sale(TradeSaleInput(
        branch_id=stock.branch.pk, trade_account_id=stock.trader.pk,
        settlement_basis=SettlementBasis.METAL, lines=(chain(stock, rate=None),)))
    assert quote.lines[0].making_rate == Decimal("40")
    assert quote.money_amount == Decimal("200.00")
    assert quote.categories[stock.chain.pk]["list_rate"] == Decimal("40")


def test_rules(stock):
    ring = TradeLineInput(barcode=stock.ring_a.barcode)
    with pytest.raises(ValidationError):  # a customer is not a trade account
        sell(stock, ring, trader=create_customer(PartyData(name="Mona")))
    with pytest.raises(ValidationError):
        sell(stock, chain(stock, grams="25"))
    with pytest.raises(ValidationError):  # two lines together exceed the lot
        sell(stock, chain(stock, grams="15"), chain(stock, grams="6"))
    with pytest.raises(ValidationError):
        sell(stock, ring, ring)
    with pytest.raises(ValidationError):
        sell(stock, TradeLineInput(barcode=stock.ring_a.barcode, making_rate="-1"))
    with pytest.raises(ValidationError):
        post_trade_sale(TradeSaleInput(branch_id=stock.branch.pk,
                                       trade_account_id=stock.trader.pk,
                                       settlement_basis="barter", lines=(ring,)))
    assert Item.objects.get(pk=stock.ring_a.pk).status == ItemStatus.IN_STOCK


def test_api_pages_and_settling(tenant_a, stock):
    client = login(tenant_a)
    made = client.post("/api/v1/parties/trade-accounts/", {"name": "Delta Gold"},
                       content_type="application/json")
    assert made.status_code == 201, made.content
    trader = made.json()["id"]
    body = {"branch": stock.branch.pk, "trade_account": trader, "settlement_basis": "metal",
            "lines": [{"item": stock.ring_a.pk, "making_rate": "50"},
                      {"category": stock.chain.pk, "karat": stock.k21.pk,
                       "gross_weight_g": "5", "qty": 1}]}
    quote = client.post("/api/v1/sales/trade/quote/", body, content_type="application/json")
    assert quote.status_code == 200, quote.content
    assert quote.json()["totals"]["fine_weight_g"] == "8.1250"
    sale = client.post("/api/v1/sales/trade/", body, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY="ts1")
    assert sale.status_code == 201, sale.content
    sale_id = sale.json()["id"]
    with tenant_context(tenant_a.id):
        line = TradeSaleLine.objects.get(trade_sale_id=sale_id, lot__isnull=False)
    back = client.post("/api/v1/sales/trade-returns/", {"sale": sale_id, "lines": [line.pk]},
                       content_type="application/json", HTTP_IDEMPOTENCY_KEY="tr1")
    assert back.status_code == 201, back.content
    for path in ("/sales/wholesale/", "/sales/wholesale/new/", f"/sales/wholesale/{sale_id}/",
                 f"/sales/wholesale/returns/{back.json()['id']}/", "/trade-accounts/",
                 "/trade-accounts/new/", f"/trade-accounts/{trader}/",
                 f"/trade-accounts/{trader}/statement/", "/settlements/new/?side=trade_account"):
        assert client.get(path).status_code == 200, path

    # The trader brings 5 g of 18K scrap and pays the making charge in cash.
    with tenant_context(tenant_a.id):
        from apps.parties.models import Party

        party = Party.objects.get(pk=trader)
        for kind, extra in (("metal_in", {"karat_id": stock.k18.pk, "gross_weight_g": "5"}),
                            ("receipt", {"method": "cash", "currency_code": "EGP",
                                         "amount": "250"})):
            post_settlement(SettlementInput(kind=kind, side="trade_account", party_id=trader,
                                            branch_id=stock.branch.pk, **extra))
        assert party_balance(party) == {}


class TestBuyingFromTraders:
    """A trade account can also sell to you: its purchases and returns post to the same
    balance as the wholesale sales."""

    def test_one_balance_both_ways(self, tenant_a, stock):
        from datetime import date

        from apps.ledger.models import Account
        from apps.ledger.statements import party_statement
        from apps.purchasing.models import SellerRole
        from apps.purchasing.returns import (
            ReturnLineInput,
            SupplierReturnInput,
            post_supplier_return,
        )
        from apps.purchasing.services import InvoiceInput, LineInput, post_invoice, save_draft

        sell(stock, chain(stock))  # they owe 4.375 fine g and 150 of making
        invoice = post_invoice(save_draft(InvoiceInput(
            supplier_id=stock.trader.pk, seller_role=SellerRole.TRADE_ACCOUNT,
            branch_id=stock.branch.pk, business_date=date.today(), currency_code="EGP",
            lines=(LineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                             gross_weight_g="10", making_cost_rate="20"),))).pk)
        assert invoice.seller_role == SellerRole.TRADE_ACCOUNT
        # 10 g of 21K = 8.75 fine g and 200 of making now owed to them.
        assert party_balance(stock.trader) == {"XAU": Decimal("-4.375"), "EGP": Decimal("-50")}
        assert stock.lot(stock.branch).gross_weight_g == Decimal("25.000")

        # 2 g go back to them at the lot's average making cost (1,700 / 25 g = 68/g).
        post_supplier_return(SupplierReturnInput(
            branch_id=stock.branch.pk, supplier_id=stock.trader.pk,
            seller_role=SellerRole.TRADE_ACCOUNT,
            lines=(ReturnLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                                   gross_weight_g="2"),)))
        assert party_balance(stock.trader) == {"XAU": Decimal("-2.625"), "EGP": Decimal("86")}
        assert balanced()

        # Everything is on the trader's one statement.
        sections = party_statement(stock.trader, Account.objects.get(role="trade_accounts"))
        numbers = {row.document_number for s in sections for row in s.rows}
        assert {"01-TS-2026-000001", invoice.number, "01-PR-2026-000001"} <= numbers

    def test_seller_must_have_the_chosen_role(self, stock):
        from datetime import date

        from apps.purchasing.models import SellerRole
        from apps.purchasing.services import InvoiceInput, LineInput, save_draft

        line = LineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                         gross_weight_g="1", making_cost_rate="0")
        with pytest.raises(ValidationError):  # the trader is not a supplier
            save_draft(InvoiceInput(supplier_id=stock.trader.pk, branch_id=stock.branch.pk,
                                    business_date=date.today(), currency_code="EGP",
                                    lines=(line,)))
        with pytest.raises(ValidationError):  # and a supplier is not a trade account
            save_draft(InvoiceInput(supplier_id=stock.ring_a.supplier_id,
                                    seller_role=SellerRole.TRADE_ACCOUNT,
                                    branch_id=stock.branch.pk, business_date=date.today(),
                                    currency_code="EGP", lines=(line,)))

    def test_screens(self, tenant_a, stock):
        client = login(tenant_a, language="en")
        page = client.get(f"/trade-accounts/{stock.trader.pk}/").content.decode()
        assert f"/purchasing/new/?from=trade_account&amp;party={stock.trader.pk}" in page
        assert f"/sales/wholesale/new/?account={stock.trader.pk}" in page
        form = client.get(f"/purchasing/new/?from=trade_account&party={stock.trader.pk}")
        html = form.content.decode()
        assert f'<option value="{stock.trader.pk}" selected>Nour Jewellers</option>' in html
        assert 'value="trade_account" class="peer sr-only" data-seller-role checked' in html
        made = client.post("/api/v1/purchasing/invoices/", {
            "supplier": stock.trader.pk, "seller_role": "trade_account",
            "branch": stock.branch.pk, "business_date": "2026-09-29", "currency": "EGP",
            "lines": [{"category": stock.chain.pk, "karat": stock.k21.pk,
                       "gross_weight_g": "3", "making_cost_rate": "10"}]},
            content_type="application/json")
        assert made.status_code == 201, made.content
        assert made.json()["seller_role"] == "trade_account"
        detail = client.get(f"/purchasing/{made.json()['id']}/").content.decode()
        assert f"/trade-accounts/{stock.trader.pk}/" in detail
        wholesale = client.get(f"/sales/wholesale/new/?account={stock.trader.pk}")
        assert f'<option value="{stock.trader.pk}" selected>' in wholesale.content.decode()
