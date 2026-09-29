import codecs
from decimal import Decimal

import pytest
from django.utils import timezone, translation

from apps.core.tenancy import tenant_context
from apps.expenses.services import ExpenseInput, create_category, post_expense
from apps.org.models import Branch
from apps.reports.registry import REPORTS
from apps.sales.services import PaymentInput, TradeInInput, post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id), translation.override("en"):
        shop = Shop()
        # Ring A: 10 g of 21K scrap in (37,050), the rest by card.
        post_sale(shop.sale(shop.line(shop.ring_a), trade_ins=[
            TradeInInput(karat_id=shop.k21.pk, gross_weight_g="10", loss_weight_g="0.5")]))
        post_sale(shop.sale(shop.line(shop.ring_b), payments=[
            PaymentInput(kind="card", currency_code="EGP", amount="22071.42")]))
        rent = create_category("Rent")
        post_expense(ExpenseInput(branch_id=shop.branch.pk, category_id=rent.pk,
                                  method="bank_transfer", currency_code="EGP", amount="1000"))
        yield shop


def run(code, **query):
    report = REPORTS[code]()
    branches = Branch.objects.all()
    params = report.parse(query, branches)
    return {table.title: table for table in report.run(params, None)}


def test_daily_summary(shop):
    with translation.override("en"):
        tables = run("daily_summary")
    sales = tables["Sales"]
    assert sales.totals["invoices"] == 2 and sales.totals["pieces"] == 2
    assert sales.totals["gross"] == Decimal("11.000")
    scrap = tables["Scrap gold received"]
    assert scrap.rows[0]["net"] == Decimal("9.500") and scrap.rows[0]["amount"] == Decimal("37050")
    boxes = tables["Cash boxes"]
    change = Decimal("37050") - RING_A_TOTAL  # handed back in cash for the big trade-in
    assert boxes.rows[0]["out"] == change and boxes.rows[0]["closing"] == -change
    banks = {row["holder"]: row for row in tables["Bank accounts and card terminals"].rows}
    assert banks["POS 1"]["in"] == Decimal("22071.42") and banks["CIB"]["out"] == 1000
    other = {row["what"]: row for row in tables["Other movements"].rows}
    assert other["Expenses"]["amount"] == 1000


def test_gold_balances_and_sales(shop):
    with translation.override("en"):
        gold = run("gold_balances")
        stock = gold["Gold on hand"]
        # Both rings sold; the 21K scrap (9.5 g net, 8.3125 fine) is what is left.
        assert [(r["karat"], r["scrap_g"]) for r in stock.rows] == [("21K", Decimal("9.500"))]
        assert stock.rows[0]["eq21"] == Decimal("9.500")
        owed = gold["Gold owed with customers, suppliers and traders"]
        assert owed.totals["fine"] == Decimal("-8.25")  # the supplier is still owed their gold

        sales = run("sales", group="karat")["Sales"]
        assert sales.totals["invoices"] == 2
        assert sales.totals["margin"] == (sales.totals["total"] - sales.totals["gold_value"]
                                          - sales.totals["making_cost"])
        by_day = run("sales", group="day")["Sales"]
        assert by_day.rows[0]["group"] == timezone.localdate()
        expenses = run("expenses")["Expenses"]
        assert expenses.rows == [{"category": "Rent", "count": 1, "total": Decimal("1000.00")}]


def test_pages_and_csv(tenant_a, shop):
    client = login(tenant_a, language="en")
    assert client.get("/reports/").status_code == 200
    for code in REPORTS:
        assert client.get(f"/reports/{code}/").status_code == 200, code
    page = client.get("/reports/sales/?group=seller&date_from=2026-01-01&date_to=2026-12-31")
    assert page.status_code == 200 and b"Sales analysis" in page.content
    csv = client.get("/reports/daily_summary/?format=csv")
    text = csv.content.decode("utf-8")
    assert csv["Content-Type"].startswith("text/csv") and csv.content.startswith(codecs.BOM_UTF8)
    assert "Scrap gold received" in text and "37050.00" in text
    assert client.get("/reports/nope/").status_code == 404
