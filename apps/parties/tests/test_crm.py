"""Finding customers to follow up (legacy GHC11 / GHC24 / GHC26) and exporting them."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context
from apps.parties import crm
from apps.parties.models import Party, PartyRoleType
from apps.parties.services import CustomerData, PartyData, create_customer
from apps.sales.models import SalesInvoice
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import Shop
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        today = timezone.localdate()
        soon = today + timedelta(days=3)
        shop.mona = create_customer(PartyData(name="Mona", phone="01001234567"), CustomerData(
            tier="vip", city="Cairo", birth_date=soon.replace(year=1990), gender="female"))
        shop.hany = create_customer(PartyData(name="Hany"), CustomerData(tier="b", city="Giza"))
        sale = post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit",
                                   customer_id=shop.mona.pk))
        SalesInvoice.objects.filter(pk=sale.pk).update(business_date=today - timedelta(days=10))
        shop.sale_total = sale.total_amount
        yield shop


def names(queryset):
    return sorted(p.name for p in queryset)


def test_filters(shop):
    every = Party.objects.filter(roles__role=PartyRoleType.CUSTOMER)
    assert names(crm.apply(every, {"tier": "vip"})[0]) == ["Mona"]
    assert names(crm.apply(every, {"city": "giza"})[0]) == ["Hany"]
    assert names(crm.apply(every, {"birthday": "week"})[0]) == ["Mona"]
    assert names(crm.apply(every, {"bought": "30"})[0]) == ["Mona"]
    assert names(crm.apply(every, {"bought": "5"})[0]) == []
    assert "Hany" in names(crm.apply(every, {"not_bought": "5"})[0])
    assert "Mona" in names(crm.apply(every, {"not_bought": "5"})[0])  # 10 days ago
    assert names(crm.apply(every, {"balance": "owes"})[0]) == ["Mona"]  # bought on credit
    mona = crm.apply(every, {"tier": "vip"})[0].get()
    assert mona.bought == shop.sale_total and mona.balance == shop.sale_total
    found, filters = crm.apply(every, {"tier": "gold", "birthday": "x", "min_total": "abc"})
    assert filters["tier"] == filters["birthday"] == filters["min_total"] == ""


def test_list_and_export(tenant_a, shop):
    client = login(tenant_a, language="en")
    page = client.get("/customers/", {"tier": "vip"}).content.decode()
    assert "Mona" in page and "Hany" not in page and "VIP" in page and "Cairo" in page
    export = client.get("/customers/", {"tier": "vip", "format": "csv"})
    assert export["Content-Type"].startswith("text/csv")
    body = export.content.decode("utf-8-sig")
    assert body.splitlines()[0].startswith("Code,Name,Phone") and "Mona" in body
    assert "Hany" not in body
    form = client.get(f"/customers/{shop.mona.pk}/").content.decode()
    assert "Customer class" in form and 'value="Cairo"' in form
