"""Partners (owners and investors): their running account in money and gold, settled through
receipts and payments, gold in and out, gold settled in money; pages, statement and opening."""

from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context
from apps.ledger.models import Account
from apps.ledger.selectors import party_balance, trial_balance
from apps.opening.services import OpeningInput, PartyBalance, post_opening
from apps.parties.services import PartyData, create_partner
from apps.sales.tests.test_sales import Shop
from apps.settlements.services import SettlementInput, post_settlement
from conftest import login

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()


def _settle(shop, kind, partner, **values):
    return post_settlement(SettlementInput(kind=kind, side="partner", party_id=partner.pk,
                                           branch_id=shop.branch.pk, **values))


def test_a_partners_account_in_money_and_gold(tenant_a, shop):
    with tenant_context(tenant_a.id):
        partner = create_partner(PartyData(name="Hassan", phone="01001234567"))
        capital = Account.objects.get(role="partners_capital")
        # puts 100,000 in, then 20 g of 21K gold
        _settle(shop, "receipt", partner, method="cash", currency_code="EGP", amount="100000")
        _settle(shop, "metal_in", partner, karat_id=shop.k21.pk, gross_weight_g="20")
        assert party_balance(partner, capital) == {"EGP": D("-100000"), "XAU": D("-17.5")}
        # the business pays him the value of 10 fine g, then hands it over in cash
        conversion = _settle(shop, "conversion", partner, direction="we_owe_gold",
                             fine_weight_g="10", price_per_fine_g="4500")
        assert conversion.amount == D("45000.00")
        _settle(shop, "payment", partner, method="cash", currency_code="EGP", amount="45000")
        assert party_balance(partner, capital) == {"EGP": D("-100000"), "XAU": D("-7.5")}
        tb = trial_balance()
        assert tb.total_debit == tb.total_credit

        post_opening(OpeningInput(branch_id=shop.branch.pk, parties=[
            PartyBalance("partner", partner.pk, "5000", False, currency_code="EGP")]))
        assert party_balance(partner, capital)["EGP"] == D("-105000")


def test_partner_pages_api_and_statement(tenant_a, shop, make_member):
    client = login(tenant_a, language="en")
    created = client.post("/api/v1/parties/partners/", {"name": "Samir", "phone": "01011112222"},
                          content_type="application/json", HTTP_IDEMPOTENCY_KEY="p1")
    assert created.status_code == 201, created.content
    pk = created.json()["id"]
    assert b"Samir" in client.get("/partners/").content
    assert client.get("/partners/new/").status_code == 200
    page = client.get(f"/partners/{pk}/").content.decode()
    assert "Samir" in page and "Pay partner" in page
    with tenant_context(tenant_a.id):
        from apps.parties.models import Party

        _settle(shop, "receipt", Party.objects.get(pk=pk), method="cash", currency_code="EGP",
                amount="2500")
    statement = client.get(f"/partners/{pk}/statement/").content.decode()
    assert "2,500.00" in statement and "Negative: what the business owes the partner" in statement
    settle_form = client.get(f"/settlements/new/?side=partner&party={pk}").content.decode()
    assert 'value="partner"' in settle_form and "Samir" in settle_form
    assert client.get("/api/v1/parties/partners/").json()["results"][0]["name"] == "Samir"

    make_member(tenant_a, "viewer", "viewer")
    viewer = login(tenant_a, username="viewer")
    assert viewer.get("/partners/").status_code == 200
    assert viewer.get("/partners/new/").status_code == 403
