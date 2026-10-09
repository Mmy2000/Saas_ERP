"""Reports pack 2: purchases, wholesale, customer returns, balances, transfers between branches,
workshop and production loss."""

from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.tenancy import tenant_context
from apps.inventory.tests.test_transfers_stocktakes import Stock
from apps.inventory.transfers import TransferInput, TransferLineInput, send_transfer
from apps.manufacturing.services import (
    IssueInput,
    IssueLineInput,
    ReceiptInput,
    issue_work_order,
    receive_work_order,
)
from apps.manufacturing.tests.test_work_orders import scrap
from apps.org.models import Branch
from apps.parties.services import PartyData, create_customer, create_trade_account
from apps.reports.registry import REPORTS
from apps.sales.models import SettlementBasis
from apps.sales.returns import create_return
from apps.sales.services import post_sale
from apps.sales.trade import TradeLineInput, TradeSaleInput, post_trade_return, post_trade_sale
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id), translation.override("en"):
        shop = Stock()  # rings A (5 g 18K) and B (6 g 18K) bought from "Factory"; 20 g 21K chain
        shop.mona = create_customer(PartyData(name="Mona"))
        shop.trader = create_trade_account(PartyData(name="Nour Jewellers"))
        sale = post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit",
                                   customer_id=shop.mona.pk))
        shop.returned = create_return(sale.pk, [sale.lines.get().pk],
                                      refund_method="customer_credit")
        shop.wholesale = post_trade_sale(TradeSaleInput(
            branch_id=shop.branch.pk, trade_account_id=shop.trader.pk,
            settlement_basis=SettlementBasis.METAL, lines=(
                TradeLineInput(category_id=shop.chain.pk, karat_id=shop.k21.pk,
                               gross_weight_g="5", qty=1, making_rate="30"),
                TradeLineInput(barcode=shop.ring_b.barcode, making_rate="50"))))
        post_trade_return(shop.wholesale.pk, [
            shop.wholesale.lines.get(item=shop.ring_b).pk])
        shop.transfer = send_transfer(TransferInput(
            from_branch_id=shop.branch.pk, to_branch_id=shop.other.pk, lines=(
                TransferLineInput(category_id=shop.chain.pk, karat_id=shop.k21.pk,
                                  gross_weight_g="4", qty=1),)))
        order = issue_work_order(IssueInput(
            branch_id=shop.branch.pk, workshop_id=None, in_house=True, lines=(
                IssueLineInput(category_id=shop.chain.pk, karat_id=shop.k21.pk,
                               gross_weight_g="2"),)))
        shop.order = receive_work_order(order.pk, ReceiptInput(lines=(scrap(shop, "1.8"),)))
        yield shop


def run(code, **query):
    with translation.override("en"):
        report = REPORTS[code]()
        params = report.parse(query, Branch.objects.all())
        return {str(table.title): table for table in report.run(params, None)}


def test_purchases(shop):
    tables = run("purchases", date_from="2026-09-01")  # the invoices are dated 1 September
    [factory] = tables["By supplier"].rows
    assert factory["supplier"] == "Factory" and factory["invoices"] == 2
    assert factory["gross"] == Decimal("31.000")  # the two rings and the 20 g chain
    by_category = {(row["category"], row["karat"]): row for row in
                   tables["By category and karat"].rows}
    assert by_category[("Chain", "21K")]["gross"] == Decimal("20.000")
    assert run("purchases", date_from="2026-09-01", family="diamonds")["By supplier"].rows == []
    only = run("purchases", date_from="2026-09-01", supplier=str(shop.ring_a.supplier_id))
    assert [row["supplier"] for row in only["By supplier"].rows] == ["Factory"]


def test_wholesale_net_of_returns(shop):
    [row] = run("wholesale")["Per trade account"].rows
    assert row["account"] == "Nour Jewellers" and row["sales"] == 1
    assert row["gross"] == Decimal("11.000") and row["ret_gross"] == Decimal("6.000")
    assert row["net_fine"] == row["fine"] - row["ret_fine"]
    assert row["net_fine"] == Decimal("4.3750")  # the 5 g of 21K chain kept


def test_customer_returns(shop):
    tables = run("customer_returns")
    [goods] = tables["By category and karat"].rows
    assert goods["karat"] == "18K" and goods["gross"] == Decimal("5.000")
    [cash] = tables["Refunds"].rows
    assert cash["returns"] == 1 and cash["refunded"] == shop.returned.refund_amount
    assert run("customer_returns", family="diamonds")["By category and karat"].rows == []


def test_balances_valued(shop):
    table = run("balances", role="trade_account")["Trade accounts"]
    [nour] = table.rows
    assert nour["name"] == "Nour Jewellers" and nour["gold"] == Decimal("4.375")
    assert nour["gold_value"] > 0 and nour["net"] >= nour["gold_value"]
    assert nour["_urls"]["name"] == f"/trade-accounts/{shop.trader.pk}/statement/"
    suppliers = run("balances", role="supplier")["Suppliers"]
    [factory] = suppliers.rows
    assert factory["net"] < 0  # we owe them
    assert run("balances", role="supplier", side="owes")["Suppliers"].rows == []
    assert run("balances", role="customer")["Customers"].rows == []  # returned to her account


def test_branch_transfers(shop):
    [route] = run("branch_transfers")["Per route and karat"].rows
    assert route["route"].endswith("→ Maadi") and route["karat"] == "21K"
    assert route["gross"] == Decimal("4.000") and route["transit"] == Decimal("4.000")


def test_production_loss(shop):
    tables = run("production_loss")
    [order] = tables["Per order"].rows
    assert order["issued"] == Decimal("1.7500") and order["received"] == Decimal("1.5750")
    assert order["loss"] == Decimal("0.1750") and order["pct"] == Decimal("10")
    assert order["_urls"]["number"] == f"/manufacturing/{shop.order.pk}/"
    [who] = tables["Per workshop or craftsman"].rows
    assert who["who"] == "In-house"
    assert run("production_loss", kind="casting")["Per order"].rows == []


def test_pages(tenant_a, shop):
    client = login(tenant_a, language="en")
    for code in ("purchases", "wholesale", "customer_returns", "balances", "branch_transfers",
                 "production_loss"):
        response = client.get(f"/reports/{code}/")
        assert response.status_code == 200, code
        assert client.get(f"/reports/{code}/?format=csv").status_code == 200, code
    assert "Wholesale sales" in client.get("/reports/").content.decode()
