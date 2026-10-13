"""Money paid in one currency against a balance kept in another, at a rate agreed on the spot
(legacy GHSS33 / GHSS34): pounds taken off a dollar balance."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.ledger.models import JournalLine
from apps.ledger.selectors import party_balance
from apps.parties.services import PartyData, create_trade_account
from apps.pricing.services import record_fx_rate
from apps.settlements.services import void_settlement
from apps.settlements.tests.test_settlements import (
    settle,
    shop,  # noqa: F401  (fixture)
)
from conftest import login

pytestmark = pytest.mark.django_db


def _owes_dollars(shop, amount="200"):
    record_fx_rate("USD", "48")
    trader = create_trade_account(PartyData(name="Nour Jewellers"))
    settle(shop, "adjustment", "trade_account", trader, asset="money", currency_code="USD",
           amount=amount, adjustment="raise", note="Opening dollars")
    return trader


def test_pounds_off_a_dollar_balance(shop):
    trader = _owes_dollars(shop)
    assert party_balance(trader) == {"USD": Decimal("200.00")}
    got = settle(shop, "receipt", "trade_account", trader, method="cash", currency_code="EGP",
                 amount="5000", balance_currency_code="USD", balance_rate="50")
    assert (got.balance_amount, got.balance_rate) == (Decimal("100.00"), Decimal("50"))
    assert party_balance(trader) == {"USD": Decimal("100.00")}  # 5,000 EGP took 100 $ off
    party_line = JournalLine.objects.get(entry=got.journal_entry, party=trader)
    assert party_line.commodity.code == "USD" and party_line.functional_amount == Decimal("-5000")
    void_settlement(got.pk)
    assert party_balance(trader) == {"USD": Decimal("200.00")}

    same = settle(shop, "receipt", "trade_account", trader, method="cash", currency_code="EGP",
                  amount="100", balance_currency_code="EGP", balance_rate="7")
    assert same.balance_currency_id is None  # same currency: an ordinary receipt
    for bad in ("", "0", "-2", "x"):
        with pytest.raises(ValidationError):
            settle(shop, "receipt", "trade_account", trader, method="cash", currency_code="EGP",
                   amount="100", balance_currency_code="USD", balance_rate=bad or None)
    with pytest.raises(ValidationError):
        settle(shop, "receipt", "trade_account", trader, method="cash", currency_code="EGP",
               amount="100", balance_currency_code="XYZ", balance_rate="5")


def test_form_api_detail_and_print(tenant_a, shop):
    with tenant_context(tenant_a.id):
        trader = _owes_dollars(shop)
    client = login(tenant_a, language="en")
    assert "Against their balance in" in client.get("/settlements/new/").content.decode()
    made = client.post("/api/v1/settlements/", {
        "kind": "receipt", "side": "trade_account", "party": trader.pk,
        "branch": shop.branch.pk, "method": "cash", "currency": "EGP", "amount": "2500",
        "balance_currency": "USD", "balance_rate": "50"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="cx1")
    assert made.status_code == 201, made.content
    pk = made.json()["id"]
    page = client.get(f"/settlements/{pk}/").content.decode()
    assert "Off their balance in" in page and "50.00 USD" in page
    printed = client.get(f"/print/settlement/{pk}/").content.decode()
    assert "Agreed rate" in printed and "50.00 USD" in printed
