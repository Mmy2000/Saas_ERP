"""Searching and filtering the document lists (Gweb's enquiry screens): words, dates, branch,
a party, the list's own choices, totals, and the new returns list."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context
from apps.org.services import BranchInput, create_branch
from apps.parties.services import PartyData, create_customer
from apps.sales.models import SalesInvoice
from apps.sales.returns import create_return
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.mona = create_customer(PartyData(name="Mona", phone="01001234567"))
        shop.first = post_sale(shop.sale(shop.line(shop.ring_a), customer_id=shop.mona.pk,
                                         payments=[shop.cash(RING_A_TOTAL)]))
        shop.second = post_sale(shop.sale(shop.line(shop.ring_b), customer_name="Walk-in Hany",
                                          customer_phone="01112223334",
                                          payments=[shop.cash(RING_A_TOTAL * 2)]))
        # The second sale was a week ago.
        SalesInvoice.objects.filter(pk=shop.second.pk).update(
            business_date=timezone.localdate() - timedelta(days=7))
        shop.other = create_branch(BranchInput(code=2, name="Maadi"))
    return shop


def test_sales_search_dates_party_and_totals(tenant_a, shop):
    client = login(tenant_a, language="en")
    every = client.get("/sales/").content.decode()
    assert shop.first.number in every and shop.second.number in every
    assert "2 documents" in every and "Total" in every

    by_number = client.get("/sales/", {"q": shop.first.number[-6:]}).content.decode()
    assert shop.first.number in by_number and shop.second.number not in by_number
    assert shop.second.number in client.get("/sales/", {"q": "Hany"}).content.decode()
    assert shop.second.number in client.get("/sales/", {"q": "0111222"}).content.decode()
    assert shop.first.number in client.get(
        "/sales/", {"q": shop.ring_a.barcode}).content.decode()

    today = timezone.localdate().isoformat()
    recent = client.get("/sales/", {"from": today}).content.decode()
    assert shop.first.number in recent and shop.second.number not in recent
    assert "1 document" in recent

    mine = client.get("/sales/", {"party": shop.mona.pk}).content.decode()
    assert shop.first.number in mine and shop.second.number not in mine
    assert "Only for" in mine and "Mona" in mine

    # Another branch has nothing; the filter keeps the paging.
    elsewhere = client.get("/sales/", {"branch": shop.other.pk}).content.decode()
    assert shop.first.number not in elsewhere and "Nothing matches" in elsewhere
    nonsense = client.get("/sales/", {"from": "not-a-date", "branch": "x", "status": "?"})
    assert nonsense.status_code == 200


def test_returns_list(tenant_a, shop):
    with tenant_context(tenant_a.id):
        line = shop.first.lines.get()
        made = create_return(shop.first.pk, [line.pk], refund_method="cash")
    client = login(tenant_a, language="en")
    page = client.get("/sales/returns/").content.decode()
    assert made.number in page and shop.first.number in page and "Refunded" in page
    assert made.number in client.get("/sales/returns/", {
        "q": shop.ring_a.barcode}).content.decode()
    assert made.number not in client.get("/sales/returns/", {
        "family": "diamond"}).content.decode()
    assert made.number in client.get("/sales/returns/", {"party": shop.mona.pk}).content.decode()


def test_every_list_takes_the_filters(tenant_a, shop):
    client = login(tenant_a, language="en")
    params = {"q": "x", "from": (date.today() - timedelta(days=30)).isoformat(),
              "to": date.today().isoformat(), "party": shop.mona.pk}
    for path in ("/sales/", "/sales/returns/", "/sales/reservations/", "/sales/wholesale/",
                 "/repairs/", "/settlements/", "/purchasing/", "/purchasing/returns/",
                 "/expenses/", "/stock/transfers/", "/treasury/movements/"):
        response = client.get(path, params)
        assert response.status_code == 200, path
        assert 'name="from"' in response.content.decode(), path
        assert client.get(path).status_code == 200, path  # with documents on the page


def test_party_pages_link_to_their_documents(tenant_a, shop):
    client = login(tenant_a, language="en")
    page = client.get(f"/customers/{shop.mona.pk}/").content.decode()
    assert f"/sales/?party={shop.mona.pk}" in page and "See their" in page
    assert f"/settlements/?party={shop.mona.pk}" in page
