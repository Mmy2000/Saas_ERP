"""Adding currencies and switching them off (legacy Cod FC1)."""

import pytest

from apps.catalog.models import Currency
from apps.catalog.services import CurrencyCommand, create_currency, update_currency
from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.org.models import Branch
from apps.pricing.services import record_fx_rate
from apps.treasury.holders import CashBoxInput, create_cash_box
from apps.treasury.models import CashBox
from conftest import login

pytestmark = pytest.mark.django_db


def test_add_and_switch_off(tenant_a):
    with tenant_context(tenant_a.id):
        pound = create_currency("gbp", CurrencyCommand())
        assert pound.code == "GBP" and pound.symbol == "£" and pound.name == ""
        assert pound.minor_units == 2
        for bad in ("GB", "G1P", "gbp"):
            with pytest.raises(ValidationError):
                create_currency(bad, CurrencyCommand())
        with pytest.raises(ValidationError):  # the company currency stays on
            update_currency(Currency.objects.get(code="EGP").pk, CurrencyCommand(is_active=False))

        branch = Branch.objects.order_by("code").first()
        box = create_cash_box(CashBoxInput(branch_id=branch.pk, currency_code="GBP"))
        with pytest.raises(ValidationError):  # a cash box still uses it
            update_currency(pound.pk, CurrencyCommand(is_active=False))
        CashBox.objects.filter(pk=box.pk).update(is_active=False)
        update_currency(pound.pk, CurrencyCommand(name="Sterling", is_active=False))
        pound.refresh_from_db()
        assert not pound.is_active and pound.label == "Sterling"


def test_pages(tenant_a):
    with tenant_context(tenant_a.id):
        record_fx_rate("USD", "48.5")
    client = login(tenant_a, language="en")
    page = client.get("/catalog/currencies/").content.decode()
    assert "USD" in page and "48.5000" in page and "Company currency" in page
    added = client.post("/catalog/currencies/new/", {"code": "KWD", "is_active": "on"})
    assert added.status_code == 302
    with tenant_context(tenant_a.id):
        kwd = Currency.objects.get(code="KWD")
    assert client.get(f"/catalog/currencies/{kwd.pk}/").status_code == 200
    again = client.post("/catalog/currencies/new/", {"code": "KWD"}).content.decode()
    assert "Already added" in again
    assert "KWD" in client.get("/pricing/fx/").content.decode()  # a rate can be published
