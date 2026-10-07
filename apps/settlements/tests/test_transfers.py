"""Balance transfers between accounts and balance adjustments (legacy GHC29, GHC25, GHSS36)."""

from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.ledger.models import Account
from apps.ledger.selectors import party_balance, trial_balance
from apps.parties.services import PartyData, create_customer
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL
from apps.settlements.services import void_settlement
from apps.settlements.tests.test_settlements import settle, shop  # noqa: F401 (fixture)
from conftest import login

pytestmark = pytest.mark.django_db


def test_a_customers_credit_pays_another_customers_debt(shop):  # noqa: F811
    mona = create_customer(PartyData(name="Mona"))
    sara = create_customer(PartyData(name="Sara"))
    post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit", customer_id=sara.pk))
    settle(shop, "receipt", "customer", mona, method="cash", currency_code="EGP",
           amount="20000")  # Mona pays in advance: she has credit with us
    transfer = settle(shop, "transfer", "customer", mona, asset="money", currency_code="EGP",
                      amount=str(RING_A_TOTAL), counter_side="customer",
                      counter_party_id=sara.pk)
    assert transfer.number == "01-BT-2026-000001"
    assert party_balance(sara) == {}
    assert party_balance(mona) == {"EGP": RING_A_TOTAL - 20000}
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit

    void_settlement(transfer.pk)
    assert party_balance(sara) == {"EGP": RING_A_TOTAL}
    assert party_balance(mona) == {"EGP": Decimal("-20000")}


def test_gold_credit_moves_to_another_kind_of_account(shop):  # noqa: F811
    mona = create_customer(PartyData(name="Mona"))
    settle(shop, "metal_in", "customer", mona, karat_id=shop.k21.pk, gross_weight_g="10")
    assert party_balance(mona) == {"XAU": Decimal("-8.75")}  # we hold her gold
    # Half of it is now owed to the supplier instead (5 g of 21K = 4.375 fine g).
    settle(shop, "transfer", "customer", mona, asset="gold", karat_id=shop.k21.pk,
           gross_weight_g="5", counter_side="supplier", counter_party_id=shop.supplier.pk)
    assert party_balance(mona) == {"XAU": Decimal("-4.375")}
    assert party_balance(shop.supplier) == {"XAU": Decimal("-12.625"), "EGP": Decimal("-1650")}


def test_adjustments_go_to_balance_adjustments(shop):  # noqa: F811
    mona = create_customer(PartyData(name="Mona"))
    post_sale(shop.sale(shop.line(shop.ring_a), payment_terms="credit", customer_id=mona.pk))
    with pytest.raises(ValidationError) as exc:  # a reason is required
        settle(shop, "adjustment", "customer", mona, asset="money", currency_code="EGP",
               amount="50", adjustment="lower")
    assert "note" in exc.value.fields
    settle(shop, "adjustment", "customer", mona, asset="money", currency_code="EGP",
           amount="50", adjustment="lower", note="Rounding the balance")
    assert party_balance(mona) == {"EGP": RING_A_TOTAL - 50}
    settle(shop, "adjustment", "supplier", shop.supplier, asset="gold", karat_id=shop.k21.pk,
           gross_weight_g="0.2", adjustment="raise", note="Weighing difference")
    assert party_balance(shop.supplier)["XAU"] == Decimal("-8.075")
    adjustments = Account.objects.get(role="balance_adjustments")
    row = next(r for r in trial_balance().rows if r.account == adjustments)
    assert row.metals == {"XAU": Decimal("-0.175")}  # gold gained, 50 EGP given away
    gold_value = row.credit - row.debit + 50
    assert gold_value > 0
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit


def test_transfer_rules(shop):  # noqa: F811
    mona = create_customer(PartyData(name="Mona"))
    with pytest.raises(ValidationError) as exc:
        settle(shop, "transfer", "customer", mona, asset="money", currency_code="EGP",
               amount="10", counter_side="customer", counter_party_id=mona.pk)
    assert "counter_party" in exc.value.fields
    with pytest.raises(ValidationError) as exc:  # Mona is not a supplier
        settle(shop, "transfer", "customer", mona, asset="money", currency_code="EGP",
               amount="10", counter_side="supplier", counter_party_id=mona.pk)
    with pytest.raises(ValidationError) as exc:
        settle(shop, "transfer", "customer", mona, asset="silver", currency_code="EGP",
               amount="10", counter_side="supplier", counter_party_id=shop.supplier.pk)
    assert "asset" in exc.value.fields


def test_api_and_pages(tenant_a):
    client = login(tenant_a)
    with tenant_context(tenant_a.id):
        from apps.parties.models import Party
        from apps.sales.tests.test_sales import Shop

        shop_ = Shop()
        supplier = Party.objects.get(name="Factory")
        mona = create_customer(PartyData(name="Mona"))
    made = client.post("/api/v1/settlements/", {
        "kind": "transfer", "side": "customer", "party": mona.pk, "branch": shop_.branch.pk,
        "asset": "money", "currency": "EGP", "amount": "100", "counter_side": "supplier",
        "counter_party": supplier.pk}, content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="bt1")
    assert made.status_code == 201, made.content
    assert made.json()["counter_party_name"] == "Factory"
    pk = made.json()["id"]
    for path in (f"/settlements/{pk}/", f"/print/settlement/{pk}/", "/settlements/",
                 "/settlements/new/?kind=transfer", f"/customers/{mona.pk}/statement/",
                 f"/suppliers/{supplier.pk}/statement/"):
        response = client.get(path)
        assert response.status_code == 200, path
    assert "Factory" in client.get(f"/settlements/{pk}/").content.decode()
