"""Reports pack 3: employees' advances and gold ordered by weight."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest
from django.utils import translation

from apps.hr.services import (
    EmployeeInput,
    create_employee,
    give_advance,
    repay_advance,
    settle_advance,
)
from apps.merchandising.tests.test_bullion import shop  # noqa: F401
from apps.org.models import Branch
from apps.parties.services import PartyData, create_customer
from apps.reports.registry import REPORTS
from apps.sales.reservations import OrderInput, ReservationInput, create_reservation
from conftest import login

pytestmark = pytest.mark.django_db


def run(code, **query):
    with translation.override("en"):
        report = REPORTS[code]()
        params = report.parse(query, Branch.objects.all())
        return {str(table.title): table for table in report.run(params, None)}


def test_employee_advances(shop):
    omar = create_employee(EmployeeInput(name="Omar", monthly_salary="3000"))
    give_advance(omar.pk, "1000", branch_id=shop.branch.pk, method="bank_transfer",
                 bank_account_id=shop.bank.pk)
    repay_advance(omar.pk, "300", branch_id=shop.branch.pk, method="bank_transfer",
                  bank_account_id=shop.bank.pk)
    settle_advance(omar.pk, "100", favour=True, branch_id=shop.branch.pk, note="Gift")
    tables = run("employee_advances")
    [row] = tables["Balances"].rows
    assert (row["given"], row["repaid"], row["settled"], row["closing"]) == (
        Decimal("1000"), Decimal("-300"), Decimal("-100"), Decimal("600"))
    statement = run("employee_advances", employee=str(omar.pk))["Statement"].rows
    assert [r["balance"] for r in statement] == [0, Decimal("1000"), Decimal("700"),
                                                 Decimal("600")]
    assert statement[1]["_urls"]["document"].startswith("/hr/advances/")


def test_weight_orders(shop):
    mona = create_customer(PartyData(name="Mona"))
    order = create_reservation(ReservationInput(
        branch_id=shop.branch.pk, customer_id=mona.pk, lines=(),
        orders=(OrderInput(category_id=shop.bars.pk, karat_id=shop.k24.pk,
                           gross_weight_g="10", qty=1),), price_locked=True))
    tables = run("weight_orders")
    [ready] = tables["To deliver"].rows
    assert (ready["category"], ready["weight"], ready["orders"]) == ("Gold bars",
                                                                    Decimal("10.000"), 1)
    [row] = tables["Orders"].rows
    assert row["customer"] == "Mona" and row["state"] == "Open"
    assert row["_urls"]["number"] == f"/sales/reservations/{order.pk}/"
    assert run("weight_orders", state="completed")["Orders"].rows == []


def test_pages(tenant_a, shop):
    client = login(tenant_a, language="en")
    for code in ("employee_advances", "weight_orders"):
        assert client.get(f"/reports/{code}/").status_code == 200, code
    index = client.get("/reports/").content.decode()
    assert "Employees&#x27; advances" in index and "Orders by weight" in index
