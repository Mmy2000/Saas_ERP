"""The reports pack: profit per piece, net sales, salespeople, customers, scrap, coverage, gold
position, category card and the branch statement."""

from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.tenancy import tenant_context
from apps.org.models import Branch
from apps.parties.services import PartyData, create_customer
from apps.reports.registry import REPORTS
from apps.reports.sales_reports import eq21
from apps.sales.returns import create_return
from apps.sales.services import TradeInInput, post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from conftest import login

pytestmark = pytest.mark.django_db
RING_B_TOTAL = Decimal("22071.42")


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id), translation.override("en"):
        shop = Shop()
        shop.mona = create_customer(PartyData(name="Mona", phone="01012345678"))
        # Ring A for cash with 10 g of 21K scrap taken in; ring B on Mona's account, returned.
        shop.sale_a = post_sale(shop.sale(shop.line(shop.ring_a), trade_ins=[
            TradeInInput(karat_id=shop.k21.pk, gross_weight_g="10", loss_weight_g="0.5")],
            payments=[shop.cash(RING_A_TOTAL - Decimal("37050"))]))
        shop.sale_b = post_sale(shop.sale(shop.line(shop.ring_b), payment_terms="credit",
                                          customer_id=shop.mona.pk))
        shop.returned = create_return(shop.sale_b.pk, [shop.sale_b.lines.get().pk],
                                      refund_method="customer_credit")
        yield shop


def run(code, **query):
    report = REPORTS[code]()
    params = report.parse(query, Branch.objects.all())
    return {table.title: table for table in report.run(params, None)}


def test_profit_per_piece_and_net_sales(shop):
    tables = run("sales_profit")
    sold = tables["Pieces sold"].rows
    # Making charged 250/g, cost 150/g: ring A (5 g) makes 500, ring B (6 g) 600.
    assert [(row["piece"], row["profit"]) for row in sold] == [
        (shop.ring_a.barcode, Decimal("500.00")), (shop.ring_b.barcode, Decimal("600.00"))]
    assert sold[0]["profit_g"] == Decimal("100")
    assert sold[0]["_urls"]["document"] == f"/sales/{shop.sale_a.pk}/"
    [back] = tables["Returned in the period"].rows
    assert back["profit"] == Decimal("-600.00") and back["weight"] == Decimal("-6.000")
    [karat] = tables["Net by karat"].rows
    assert karat["pieces"] == 1 and karat["profit"] == Decimal("500.00")
    assert karat["eq21"] == eq21(Decimal("3.75")) == Decimal("4.286")
    assert run("sales_profit", show="losses")["Pieces sold"].rows == []

    [net] = run("net_sales")["Net sales"].rows
    assert net["sold"] == RING_A_TOTAL + RING_B_TOTAL and net["returned"] == RING_B_TOTAL
    assert net["net"] == RING_A_TOTAL and net["net_g"] == Decimal("5.000")
    assert net["profit"] == Decimal("500.00")
    [person] = run("salespeople")["Salespeople"].rows
    assert person["rank"] == 1 and person["net21"] == Decimal("4.286")
    assert person["per_g"] == Decimal("500.00") / Decimal("4.286")

    [customer] = run("customer_analysis")["Customers"].rows
    assert customer["name"] == "Mona" and customer["bought"] == RING_B_TOTAL
    assert customer["net"] == 0 and not customer["owes"]  # returned to her account


def test_scrap_coverage_and_gold_position(shop):
    tables = run("scrap")
    [taken] = tables["Bought"].rows
    # 9.5 g of 21K net at 3900/g = 37,050; the day's selling price is 4000/g.
    assert taken["net"] == Decimal("9.500") and taken["paid"] == Decimal("37050.00")
    assert taken["value"] == Decimal("38000.00") and taken["margin"] == Decimal("950.00")
    [on_hand] = tables["Scrap on hand now"].rows
    assert on_hand["gross"] == Decimal("9.500") and on_hand["cost"] == Decimal("37050.00")

    coverage = run("coverage")
    sold, bought, covered, open_ = coverage["Coverage"].rows
    assert sold["grams"] == eq21(Decimal("3.75"))  # ring B came back
    assert bought["grams"] == Decimal("9.500")  # 21K is its own equivalent
    assert bought["price"] == Decimal("37050") / Decimal("9.5")
    assert covered["grams"] == sold["grams"]
    assert covered["amount"] == (sold["price"] - bought["price"]) * covered["grams"]
    assert open_["grams"] == Decimal("9.500") - eq21(Decimal("3.75"))

    position = run("gold_position")["Gold position"]
    held = {row["account"]: row["fine"] for row in position.rows if row["group"] == "Held"}
    assert any("Scrap" in name for name in held)
    # Ring B (4.5 fine g) and 9.5 g of 21K scrap (8.3125 fine g) are held; the supplier is
    # owed 8.25 fine g for both rings.
    assert position.totals["fine"] == Decimal("4.5") + Decimal("8.3125") - Decimal("8.25")


def test_category_card_and_branch_statement(shop):
    tables = run("stock_card", date_from="2026-01-01")
    rows = {(row["category"], row["karat"]): row for row in tables["Opening and closing"].rows}
    rings = rows[("Rings", "18K")]
    assert (rings["in_qty"], rings["out_qty"], rings["close_qty"]) == (3, 2, 1)
    assert rings["close_g"] == Decimal("6.000")
    kinds = {row["kind"]: row for row in tables["By kind of movement"].rows}
    assert kinds["Sale"]["qty"] == -2 and kinds["Customer return"]["qty"] == 1
    assert tables["Movements"].rows[-1]["balance"] == Decimal("15.500")  # rings + scrap
    from apps.catalog.models import ItemCategory

    rings_only = run("stock_card", date_from="2026-01-01",
                     category=str(ItemCategory.objects.get(code="1010").pk))
    assert len(rings_only["Opening and closing"].rows) == 1

    statement = run("branch_statement")
    assert statement["Balances between branches"].rows == []


def test_pages(tenant_a, shop):
    client = login(tenant_a, language="en")
    index = client.get("/reports/").content.decode()
    assert "Gold and stock" in index and "Buy and sell coverage" in index
    for code in ("sales_profit", "net_sales", "salespeople", "customer_analysis", "scrap",
                 "coverage", "gold_position", "stock_card", "branch_statement"):
        assert client.get(f"/reports/{code}/").status_code == 200, code
        assert client.get(f"/reports/{code}/?format=csv").status_code == 200, code
    with tenant_context(tenant_a.id):
        branch = Branch.objects.get(code=1)
    page = client.get(f"/reports/branch_statement/?branch={branch.pk}")
    assert page.status_code == 200 and "Statement of" in page.content.decode()
    assert client.get("/reports/net_sales/?group=seller").status_code == 200
