from datetime import date
from decimal import Decimal

import pytest
from django.db.models import Sum
from django.utils import timezone, translation

from apps.core.tenancy import tenant_context
from apps.ledger import closing
from apps.ledger.models import JournalLine
from apps.ledger.tests.test_posting import Books
from apps.reports.tests.test_reports import run, shop  # noqa: F401
from conftest import login

pytestmark = pytest.mark.django_db


def _rows(table):
    return {row["label"]: row for row in table.rows}


def _profit():
    """Net profit straight from the ledger: everything posted to income and expenses."""
    total = JournalLine.objects.filter(account__type__in=("income", "expense")).aggregate(
        f=Sum("functional_amount"))["f"]
    return -(total or 0)


def test_profit_and_loss(shop):  # noqa: F811
    with translation.override("en"):
        table = run("profit_loss", columns="period")["Profit and loss"]
    rows = _rows(table)
    assert rows["Net profit"]["amount"] == _profit()
    assert rows["Gross profit"]["amount"] == (rows["Total sales"]["amount"]
                                               - rows["Total cost of sales"]["amount"])
    assert rows["Total operating expenses"]["amount"] == Decimal("1000")  # the rent
    assert rows["Total sales"]["share"] == 100
    sold = next(row for label, row in rows.items() if "Cost of gold sold" in label)
    assert sold["metal_gold"] > 0  # the fine gold that left stock with the rings

    with translation.override("en"):
        before = _rows(run("profit_loss", columns="previous")["Profit and loss"])
        months = run("profit_loss", columns="months")["Profit and loss"]
        last_year = _rows(run("profit_loss", columns="last_year")["Profit and loss"])
    assert before["Net profit"]["before"] == 0 and before["Net profit"]["change"] == _profit()
    assert last_year["Net profit"]["before"] == 0
    assert _rows(months)["Net profit"]["total"] == _profit()
    assert [c.key for c in months.columns][-1] == "total"


def test_balance_sheet_balances(shop):  # noqa: F811
    with translation.override("en"):
        table = run("balance_sheet")["Balance sheet"]
    rows = _rows(table)
    assets, both = rows["Total assets"], rows["Total liabilities and equity"]
    assert assets["amount"] == both["amount"] and assets["amount"] > 0
    assert assets.get("metal_gold") == both.get("metal_gold")  # gold balances too
    assert rows["Profit not yet closed"]["amount"] == _profit()
    assert "Difference with other branches" not in rows
    assert any(label.startswith("1101 ") for label in rows)  # cash boxes roll up


def test_closed_year_moves_into_retained_earnings(tenant_a):
    year = timezone.localdate().year - 1
    with tenant_context(tenant_a.id), translation.override("en"):
        books = Books()
        books.post(books.line("cash", books.egp, "1000"),
                   books.line("repair_income", books.egp, "-1000"), on=date(year, 6, 1))
        for month in range(6, 13):
            closing.close_month(year, month)
        closing.close_year(year)
        sheet = _rows(run("balance_sheet")["Balance sheet"])
        assert sheet["Profit not yet closed"]["amount"] == 0
        assert sheet["3201 Retained earnings"]["amount"] == 1000
        # The closing entry is left out: the year still shows its profit.
        pnl = _rows(run("profit_loss", date_from=f"{year}-01-01", date_to=f"{year}-12-31",
                        columns="period")["Profit and loss"])
        assert pnl["Net profit"]["amount"] == 1000


def test_pages_and_csv(tenant_a, shop):  # noqa: F811
    client = login(tenant_a, language="en")
    for path in ("/accounting/profit-and-loss/", "/accounting/balance-sheet/",
                 "/reports/profit_loss/?columns=months", "/reports/profit_loss/?columns=period",
                 "/reports/balance_sheet/?branch=" + str(shop.branch.pk)):
        response = client.get(path)
        assert response.status_code == 200, path
    page = client.get("/accounting/profit-and-loss/").content.decode()
    assert "Gross profit" in page and "Net profit" in page
    csv = client.get("/accounting/balance-sheet/?format=csv").content.decode("utf-8-sig")
    assert "Total liabilities and equity" in csv
