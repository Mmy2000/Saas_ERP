"""Editing the chart of accounts (legacy Acc GHG11-GHG15)."""

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.ledger.accounts import (
    AccountInput,
    create_account,
    delete_account,
    suggest_code,
    update_account,
)
from apps.ledger.models import Account
from apps.ledger.services import LineInput, account_for, functional_commodity, post_entry
from apps.org.models import Branch
from conftest import login

pytestmark = pytest.mark.django_db


def group(code):
    return Account.objects.get(code=code)


def test_codes_follow_the_group(tenant_a):
    with tenant_context(tenant_a.id):
        assert suggest_code(group("5")) == "5402"  # after 5401 Rounding differences
        assert suggest_code(group("21")) == "2105"
        vehicles = create_account(AccountInput(parent_id=group("5").pk, code="", name="Vehicles",
                                               is_group=True))
        assert vehicles.code == "5402" and not vehicles.is_postable
        assert vehicles.type == "expense" and vehicles.nature == "debit"
        assert suggest_code(vehicles) == "540201"
        fuel = create_account(AccountInput(parent_id=vehicles.pk, code="", name="Fuel"))
        assert fuel.code == "540201" and fuel.is_postable and fuel.commodity_scope == "money"
        with pytest.raises(ValidationError):  # not under its group's code
            create_account(AccountInput(parent_id=vehicles.pk, code="5999", name="X"))
        with pytest.raises(ValidationError):  # taken
            create_account(AccountInput(parent_id=vehicles.pk, code="540201", name="X"))
        with pytest.raises(ValidationError):  # not a group
            create_account(AccountInput(parent_id=fuel.pk, code="", name="X"))
        with pytest.raises(ValidationError):  # cash boxes are opened in Treasury
            create_account(AccountInput(parent_id=group("1101").pk, code="", name="Safe"))
        loan = create_account(AccountInput(parent_id=group("21").pk, code="", name="Bank loan",
                                           currency_code="USD"))
        assert loan.commodity.code == "USD" and loan.nature == "credit"


def test_switch_off_and_delete(tenant_a):
    with tenant_context(tenant_a.id):
        branch = Branch.objects.order_by("code").first()
        travel = create_account(AccountInput(parent_id=group("5").pk, code="", name="Travel"))
        unused = create_account(AccountInput(parent_id=group("5").pk, code="", name="Spare"))
        post_entry(branch=branch, business_date=timezone.localdate(), kind="manual",
                   lines=[LineInput(account=travel, commodity=functional_commodity(),
                                    quantity=Decimal("100")),
                          LineInput(account=account_for("opening_equity"),
                                    commodity=functional_commodity(),
                                    quantity=Decimal("-100"))])
        with pytest.raises(ValidationError):  # 100 still on it
            update_account(travel.pk, name="Travel", is_active=False)
        with pytest.raises(DomainError):  # posted to: switch it off instead
            delete_account(travel.pk)
        with pytest.raises(ValidationError):  # the system posts to it
            update_account(account_for("expenses").pk, name="", is_active=False)
        renamed = update_account(account_for("expenses").pk, name="General costs",
                                 is_active=True)
        assert renamed.label == "General costs"
        assert update_account(renamed.pk, name="", is_active=True).name == ""  # standard name
        with pytest.raises(DomainError):  # a standard account
            delete_account(group("5").pk)
        delete_account(unused.pk)
        assert not Account.objects.filter(pk=unused.pk).exists()


def test_pages(tenant_a):
    client = login(tenant_a, language="en")
    with tenant_context(tenant_a.id):
        parent = group("5")
    page = client.get("/accounting/accounts/").content.decode()
    assert "New account" in page and f"/accounting/accounts/new/?parent={parent.pk}" in page
    form = client.get(f"/accounting/accounts/new/?parent={parent.pk}").content.decode()
    assert 'value="5402"' in form
    created = client.post("/accounting/accounts/new/", {
        "parent": parent.pk, "is_group": "0", "code": "", "name": "Insurance",
        "nature": "debit", "commodity_scope": "money"})
    assert created.status_code == 302
    with tenant_context(tenant_a.id):
        insurance = Account.objects.get(name="Insurance")
    assert client.get(f"/accounting/accounts/{insurance.pk}/").status_code == 200
    off = client.post(f"/accounting/accounts/{insurance.pk}/", {"name": "Insurance"})
    assert off.status_code == 302
    assert "Insurance" not in client.get("/accounting/accounts/").content.decode()
    assert "Insurance" in client.get("/accounting/accounts/?inactive=1").content.decode()
    gone = client.post(f"/accounting/accounts/{insurance.pk}/", {"delete": "1"})
    assert gone.status_code == 302
    with tenant_context(tenant_a.id):
        assert not Account.objects.filter(name="Insurance").exists()
    one_currency = client.post("/accounting/accounts/new/", {
        "parent": parent.pk, "is_group": "0", "name": "X", "nature": "debit",
        "commodity_scope": "currency"}).content.decode()
    assert "Choose the currency" in one_currency
