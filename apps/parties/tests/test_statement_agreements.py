"""Agreed statements with traders and suppliers (legacy Suu2_Match, GHSS40 / GHSS41)."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.parties.agreements import agree_statement, agreements_for
from apps.parties.models import StatementAgreement
from apps.parties.services import PartyData, create_trade_account
from apps.settlements.services import void_settlement
from apps.settlements.tests.test_settlements import (
    settle,
    shop,  # noqa: F401  (fixture)
)
from conftest import login

pytestmark = pytest.mark.django_db


def _trader(shop):
    trader = create_trade_account(PartyData(name="Nour Jewellers"))
    settle(shop, "metal_in", "trade_account", trader, karat_id=shop.k21.pk, gross_weight_g="10")
    return trader


def test_agree_then_a_change_is_flagged(shop):
    trader = _trader(shop)
    agreed = agree_statement(trader, "trade_account", "trade_accounts", agreed_with="Hany")
    assert agreed.balances == {"XAU": "-8.7500"} and agreed.agreed_with == "Hany"
    [view] = agreements_for(trader, "trade_account", "trade_accounts")
    assert view.holds and view.lines == [("XAU", Decimal("-8.75"), Decimal("-8.75"), 4)]

    # A document dated on or before the agreed day moves that day's balance: flagged.
    later = settle(shop, "metal_in", "trade_account", trader, karat_id=shop.k21.pk,
                   gross_weight_g="4")
    view = agreements_for(trader, "trade_account", "trade_accounts")[0]
    assert not view.holds and view.lines[0][2] == Decimal("-12.25")
    void_settlement(later.pk)  # voided: the agreed balance holds again
    assert agreements_for(trader, "trade_account", "trade_accounts")[0].holds

    with pytest.raises(ValidationError):
        agree_statement(trader, "trade_account", "trade_accounts",
                        as_of=timezone.localdate() + timedelta(days=1))


def test_back_dated_agreement(shop):
    trader = _trader(shop)
    yesterday = timezone.localdate() - timedelta(days=1)
    agreement = agree_statement(trader, "trade_account", "trade_accounts", as_of=yesterday)
    assert agreement.balances == {}  # nothing was owed yet
    [view] = agreements_for(trader, "trade_account", "trade_accounts")
    assert view.holds and view.lines == []


def test_statement_page(tenant_a, shop):
    with tenant_context(tenant_a.id):
        trader = _trader(shop)
    client = login(tenant_a, language="en")
    url = f"/trade-accounts/{trader.pk}/statement/"
    page = client.get(url).content.decode()
    assert "Agreed balances" in page and "Save agreed balance" in page
    answer = client.post(url, {"as_of": timezone.localdate().isoformat(),
                               "agreed_with": "Hany", "note": "Monthly check"})
    assert answer.status_code == 302
    page = client.get(url).content.decode()
    assert "Hany" in page and "Still matches" in page and "-8.7500 XAU" in page
    with tenant_context(tenant_a.id):
        pk = StatementAgreement.objects.get().pk
    client.post(url, {"delete": pk})
    with tenant_context(tenant_a.id):
        assert not StatementAgreement.objects.exists()
