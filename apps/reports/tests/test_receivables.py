"""Unpaid invoices (oldest paid first, ageing) and customer visits."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone, translation

from apps.core.tenancy import tenant_context
from apps.org.models import Branch
from apps.parties.services import PartyData, create_customer
from apps.reports.registry import REPORTS
from apps.sales.services import post_sale, void_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from apps.settlements.services import SettlementInput, post_settlement
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id), translation.override("en"):
        yield Shop()


def run(code, **query):
    report = REPORTS[code]()
    params = report.parse(query, Branch.objects.all())
    return {table.title: table for table in report.run(params, None)}


def receipt(shop, customer, amount):
    return post_settlement(SettlementInput(
        kind="receipt", side="customer", party_id=customer.pk, branch_id=shop.branch.pk,
        method="cash", currency_code="EGP", amount=amount))


def test_payments_clear_the_oldest_invoices_first(shop):
    mona = create_customer(PartyData(name="Mona", phone="01012345678"))
    first = post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit",
                                customer_id=mona.pk))
    cancelled = post_sale(shop.sale(shop.line(shop.ring_b), payment_terms="credit",
                                    customer_id=mona.pk))
    void_sale(cancelled.pk)
    second = post_sale(shop.sale(shop.line(shop.ring_b), payment_terms="credit",
                                 customer_id=mona.pk))
    receipt(shop, mona, "5000")

    tables = run("unpaid_invoices")
    items = tables["Open invoices"].rows
    assert [(row["document"], row["open"]) for row in items] == [
        (f"Sales invoice {first.number}", RING_A_TOTAL - 5000),
        (f"Sales invoice {second.number}", second.total_amount)]
    assert items[0]["_urls"]["document"] == f"/sales/{first.pk}/"
    [ageing] = tables["By age"].rows
    assert ageing["d30"] == RING_A_TOTAL - 5000 + second.total_amount
    assert ageing["phone"] == "01012345678"

    later = timezone.localdate() + timedelta(days=45)
    [ageing] = run("unpaid_invoices", date=later.isoformat())["By age"].rows
    assert ageing["d30"] == 0 and ageing["d60"] == RING_A_TOTAL - 5000 + second.total_amount

    receipt(shop, mona, str(RING_A_TOTAL - 5000))  # the first invoice is paid off
    items = run("unpaid_invoices")["Open invoices"].rows
    assert [row["document"] for row in items] == [f"Sales invoice {second.number}"]
    receipt(shop, mona, str(second.total_amount))
    assert run("unpaid_invoices")["Open invoices"].rows == []


def test_visits_new_returning_and_not_back_lately(shop):
    mona = create_customer(PartyData(name="Mona", phone="01012345678"))
    post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit", customer_id=mona.pk))
    receipt(shop, mona, "1000")  # same day: still one visit
    # A walk-in who gave Mona's number is Mona.
    post_sale(shop.sale(shop.line(shop.ring_b), payments=[shop.cash(Decimal("22071.42"))],
                        customer_name="M.", customer_phone="+20 10 1234 5678"))
    tables = run("customer_visits")
    [person] = tables["Customers"].rows
    assert person["name"] == "Mona" and person["visits"] == 1 and person["kind"] == "New"
    assert person["value"] == RING_A_TOTAL + Decimal("22071.42")
    assert len(tables["Visits"].rows) == 3
    assert run("customer_visits", kind="returning")["Customers"].rows == []

    tomorrow = timezone.localdate() + timedelta(days=1)
    later = run("customer_visits", date_from=tomorrow.isoformat(), date_to=tomorrow.isoformat())
    assert later["Customers"].rows == []

    in_100_days = timezone.localdate() + timedelta(days=100)
    lapsed = run("customer_visits", date_from=in_100_days.isoformat(),
                 date_to=in_100_days.isoformat())["Not back lately"].rows
    assert [(row["name"], row["days"]) for row in lapsed] == [("Mona", 100)]


def test_pages(tenant_a, shop):
    client = login(tenant_a, language="en")
    mona = create_customer(PartyData(name="Mona"))
    with tenant_context(tenant_a.id):
        sale = post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit",
                                   customer_id=mona.pk))
    page = client.get("/reports/unpaid_invoices/")
    assert page.status_code == 200 and f'href="/sales/{sale.pk}/"' in page.content.decode()
    assert client.get("/reports/unpaid_invoices/?format=csv").status_code == 200
    assert client.get("/reports/unpaid_invoices/?who=trade_accounts").status_code == 200
    visits = client.get("/reports/customer_visits/")
    assert visits.status_code == 200 and "Mona" in visits.content.decode()
