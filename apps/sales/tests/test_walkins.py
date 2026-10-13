"""A walk-in buyer made a customer, with their other walk-in sales (by phone)."""

import pytest

from apps.core.errors import DomainError
from apps.core.tenancy import tenant_context
from apps.parties.models import Party
from apps.parties.services import PartyData, create_customer
from apps.sales.models import SalesInvoice
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from apps.sales.walkins import make_customer
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.first = post_sale(shop.sale(shop.line(shop.ring_a), customer_name="Hany",
                                         customer_phone="0111 222 3334",
                                         payments=[shop.cash(RING_A_TOTAL)]))
        shop.second = post_sale(shop.sale(shop.line(shop.ring_b), customer_name="Hany S.",
                                          customer_phone="+201112223334",
                                          payments=[shop.cash(RING_A_TOTAL * 2)]))
        yield shop


def test_make_customer_links_the_same_phone(shop):
    party, linked = make_customer(shop.first.pk, name="Hany Samir")
    assert party.name == "Hany Samir" and party.phone_e164 == "+201112223334" and linked == 2
    assert set(SalesInvoice.objects.filter(customer=party).values_list("pk", flat=True)) == {
        shop.first.pk, shop.second.pk}
    with pytest.raises(DomainError):  # already a customer's sale
        make_customer(shop.first.pk)


def test_existing_customer_with_the_phone(shop):
    known = create_customer(PartyData(name="Hany (known)", phone="01112223334"))
    party, linked = make_customer(shop.second.pk)
    assert party == known and linked == 2
    assert Party.objects.filter(phone_e164="+201112223334").count() == 1


def test_button_and_api(tenant_a, shop):
    client = login(tenant_a, language="en")
    page = client.get(f"/sales/{shop.first.pk}/").content.decode()
    assert "Make a customer" in page
    made = client.post(f"/api/v1/sales/invoices/{shop.first.pk}/make-customer/",
                       {"name": "Hany"}, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY="wk1")
    assert made.status_code == 200, made.content
    assert made.json()["linked"] == 2
    assert "Make a customer" not in client.get(f"/sales/{shop.first.pk}/").content.decode()
