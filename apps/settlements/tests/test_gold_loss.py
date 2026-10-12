"""Gold received from a trader or customer with a loss taken off (legacy GHSH15a خسية), and scrap
bought on a trade account."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.inventory.models import LotBalance
from apps.ledger.selectors import party_balance
from apps.parties.services import PartyData, create_trade_account
from apps.purchasing.tests.test_scrap import (
    buy,
    scrap_g,
    shop,  # noqa: F401  (fixture)
)
from apps.settlements.services import void_settlement
from apps.settlements.tests.test_settlements import settle
from conftest import login

pytestmark = pytest.mark.django_db


def test_gold_in_with_a_loss(shop):
    trader = create_trade_account(PartyData(name="Nour Jewellers"))
    before = scrap_g(shop)
    got = settle(shop, "metal_in", "trade_account", trader, karat_id=shop.k21.pk,
                 gross_weight_g="10", loss_weight_g="0.4")
    assert got.loss_weight_g == Decimal("0.400") and got.net_weight_g == Decimal("9.600")
    assert got.fine_weight_g == Decimal("8.4000")  # 9.6 g × 875‰
    assert party_balance(trader) == {"XAU": Decimal("-8.4000")}  # credited what counts
    assert scrap_g(shop) == before + Decimal("9.600")
    for bad in ("10", "-1", "x"):
        with pytest.raises(ValidationError):
            settle(shop, "metal_in", "trade_account", trader, karat_id=shop.k21.pk,
                   gross_weight_g="10", loss_weight_g=bad)
    void_settlement(got.pk)  # the net weight goes back out
    assert scrap_g(shop) == before and party_balance(trader) == {}
    settle(shop, "metal_in", "trade_account", trader, karat_id=shop.k21.pk,
           gross_weight_g="3")
    out = settle(shop, "metal_out", "trade_account", trader, karat_id=shop.k21.pk,
                 gross_weight_g="1", loss_weight_g="0.5")
    assert out.loss_weight_g == 0  # a loss only applies to gold coming in
    assert scrap_g(shop) == before + 2


def test_scrap_bought_on_a_trade_account(shop):
    trader = create_trade_account(PartyData(name="Nour Jewellers"))
    bought = buy(shop, weight="5", payment="account", party_id=trader.pk,
                 party_role="trade_account", seller_name="")
    assert party_balance(trader) == {"EGP": -bought.total_amount}  # we owe the trader
    assert LotBalance.objects.filter(lot__category__code="SCRAP").exists()


def test_form_and_detail(tenant_a, shop):
    with tenant_context(tenant_a.id):
        trader = create_trade_account(PartyData(name="Nour Jewellers"))
    client = login(tenant_a, language="en")
    assert "Loss (per cent)" in client.get("/settlements/new/").content.decode()
    made = client.post("/api/v1/settlements/", {
        "kind": "metal_in", "side": "trade_account", "party": trader.pk,
        "branch": shop.branch.pk, "karat": shop.k21.pk, "gross_weight_g": "20",
        "loss_weight_g": "1"}, content_type="application/json", HTTP_IDEMPOTENCY_KEY="gl1")
    assert made.status_code == 201, made.content
    page = client.get(f"/settlements/{made.json()['id']}/").content.decode()
    assert "Net weight" in page and "19.000" in page
    assert "Trade account" in client.get("/purchasing/scrap/buy/").content.decode()
