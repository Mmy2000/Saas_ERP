"""Reports pack 4 and the report filters from the third pass: goods (gold / diamonds) on the
sales reports, payroll over months, cash and bank on a day, purchases against their invoices,
label changes, and the daily summary over several days or a shift."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone, translation

from apps.hr.services import post_payroll
from apps.hr.tests.test_payroll import M, Y
from apps.hr.tests.test_payroll import shop as hr_shop  # noqa: F401
from apps.inventory.models import Item
from apps.org.models import Branch
from apps.reports.registry import REPORTS
from apps.sales.tests.test_sales import Shop
from apps.treasury.tests.test_treasury import cash_sale
from conftest import login

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


def run(code, **query):
    with translation.override("en"):
        report = REPORTS[code]()
        params = report.parse(query, Branch.objects.all())
        return {str(table.title): table for table in report.run(params, None)}


@pytest.fixture
def shop(tenant_a):
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant_a.id):
        shop = Shop()
        cash_sale(shop)
        yield shop


def test_goods_filter_on_sales_reports(shop):
    gold = run("net_sales", family="gold")["Net sales"]
    assert gold.totals["pieces"] == 1
    assert run("net_sales", family="diamonds")["Net sales"].rows == []
    people = run("salespeople", family="diamonds")["Salespeople"]
    assert "net21" not in [c.key for c in people.columns] and people.rows == []
    assert run("sales_profit", family="diamonds")["Pieces sold"].rows == []
    assert len(run("sales_profit")["Pieces sold"].rows) == 1


def test_payroll(hr_shop):
    post_payroll(Y, M, branch_id=hr_shop.branch.pk)
    tables = run("payroll", date_from=f"{Y}-{M:02d}-01")
    names = {row["employee"].split(" · ")[1]: row for row in tables["Per employee"].rows}
    assert names["Omar"]["salary"] == Decimal("3000") and names["Omar"]["months"] == 1
    [month] = tables["Per month"].rows
    assert month["month"] == f"{Y}-{M:02d}" and month["people"] == 2
    assert month["net"] == tables["Per employee"].totals["net"]
    omar = names["Omar"]["_urls"]["employee"].rstrip("/").split("/")[-1]
    only = run("payroll", date_from=f"{Y}-{M:02d}-01", employee=omar)["Per employee"].rows
    assert len(only) == 1


def test_cash_balances(shop):
    [box] = run("cash_balances")["Cash boxes"].rows
    assert box["balance"] > 0 and box["currency"] == "EGP"
    before = run("cash_balances", date=(TODAY - timedelta(days=1)).isoformat())
    assert before["Cash boxes"].rows == []


def test_label_changes(shop):
    Item.objects.filter(pk=shop.ring_a.pk).update(label_price=Decimal("9999"))
    [row] = run("label_changes")["Changes"].rows
    assert row["piece"] == shop.ring_a.barcode and row["what"] == "Label price"
    assert row["after"] == "9,999.00"


def test_daily_summary_range_and_shift(shop):
    whole = run("daily_summary", date=TODAY.isoformat())
    assert whole["Sales"].totals["invoices"] == 1
    hour = timezone.localtime().replace(second=0, microsecond=0)
    later = (hour + timedelta(minutes=2)).strftime("%H:%M")
    if hour.hour < 23:  # a shift starting after the sale: nothing in it
        shift = run("daily_summary", date=TODAY.isoformat(), from_time=later)
        assert not shift["Sales"].rows
    two_days = run("daily_summary", date=(TODAY - timedelta(days=1)).isoformat(),
                   until=TODAY.isoformat())
    assert two_days["Sales"].totals["invoices"] == 1


def test_pages(tenant_a, shop):
    client = login(tenant_a, language="en")
    for code in ("payroll", "cash_balances", "coding_differences", "label_changes"):
        assert client.get(f"/reports/{code}/").status_code == 200, code
    page = client.get("/reports/daily_summary/", {"from_time": "08:00", "to_time": "16:00"})
    assert page.status_code == 200 and "08:00–16:00" in page.content.decode()
