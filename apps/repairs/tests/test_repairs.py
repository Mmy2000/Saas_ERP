from decimal import Decimal

import pytest

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.ledger.models import Account
from apps.ledger.selectors import party_balance, trial_balance
from apps.parties.services import PartyData, create_customer, create_workshop
from apps.repairs.models import RepairKind, RepairOrder
from apps.repairs.services import (
    ReadyLineInput,
    RefundMethod,
    RepairInput,
    RepairLineInput,
    add_deposit,
    cancel_repair,
    create_repair,
    deliver,
    mark_ready,
    send_to_workshop,
)
from apps.sales.services import PaymentInput
from apps.sales.tests.test_sales import Shop
from apps.treasury.holders import balance, default_cash_box
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.customer = create_customer(PartyData(name="Mona", phone="01012345678"))
        shop.workshop = create_workshop(PartyData(name="Atef Workshop"))
        yield shop


def take_in(shop, *, customer=True, deposit=None, kind=RepairKind.REPAIR, **kw):
    return create_repair(RepairInput(
        branch_id=shop.branch.pk, kind=kind,
        customer_id=shop.customer.pk if customer else None,
        customer_name="" if customer else "Walk-in Hany", customer_phone="01099990000",
        lines=(RepairLineInput("Ring: resize to 17", karat_id=shop.k21.pk, weight_in_g="4.5",
                               charge="150"),
               RepairLineInput("Chain: new clasp", karat_id=shop.k18.pk, weight_in_g="8",
                               charge="100")),
        deposit=shop.cash(deposit) if deposit else None, **kw))


def ready(order, out=("4.45", "8"), charges=(None, None), labour="0"):
    lines = list(order.lines.order_by("id"))
    return mark_ready(order.pk, tuple(ReadyLineInput(line.pk, w, c) for line, w, c in
                                      zip(lines, out, charges, strict=True)),
                      labour_amount=labour)


def cash(shop) -> Decimal:
    return balance(default_cash_box(shop.branch, shop.bank.currency).account_id)[0]


def held(shop) -> Decimal:
    return -party_balance(shop.customer, Account.objects.get(role="customer_deposits")).get(
        "EGP", Decimal(0))


def roles():
    return {r.account.role: r for r in trial_balance().rows}


def balanced():
    tb = trial_balance()
    return tb.total_debit == tb.total_credit


def test_full_flow_through_a_workshop(shop):
    order = take_in(shop, deposit="100")
    assert order.number == "01-RP-2026-000001" and order.bag_number == 1
    assert order.state == "received" and order.charge_amount == Decimal("250")
    assert held(shop) == 100 and cash(shop) == 100
    assert take_in(shop).bag_number == 2  # bag numbers count up per branch

    send_to_workshop(order.pk, shop.workshop.pk)
    order.refresh_from_db()
    assert order.state == "at_workshop"

    # Back: the ring lost 0.05 g; resizing cost more than quoted; the workshop charges 120.
    order = ready(order, charges=("180", None), labour="120")
    assert order.state == "ready" and order.charge_amount == Decimal("280")
    assert order.lines.order_by("id").first().loss_g == Decimal("0.050")
    assert party_balance(shop.workshop) == {"EGP": Decimal("-120")}
    assert roles()["repair_costs"].debit == Decimal("120")

    # 280 due, 100 held: the customer pays 200 cash and gets 20 back.
    order = deliver(order.pk, payments=(shop.cash("200"),))
    assert order.state == "delivered"
    assert (order.paid_amount, order.change_amount) == (Decimal("200"), Decimal("20"))
    assert held(shop) == 0 and cash(shop) == Decimal("280")
    assert roles()["repair_income"].credit == Decimal("280")
    assert party_balance(shop.customer) == {}
    assert balanced()


def test_walk_in_done_in_the_shop(shop):
    order = take_in(shop, customer=False)
    assert order.who == "Walk-in Hany"
    with pytest.raises(ValidationError):  # deposits are held for registered customers
        add_deposit(order.pk, shop.cash("50"))
    with pytest.raises(ValidationError):  # no workshop, no labour charge
        ready(order, labour="30")
    with pytest.raises(DomainError):  # not ready yet
        deliver(order.pk, payments=(shop.cash("250"),))
    ready(order)
    with pytest.raises(DomainError):  # 250 due
        deliver(order.pk, payments=(shop.cash("200"),))
    with pytest.raises(ValidationError):  # a walk-in cannot pay later
        deliver(order.pk, on_account=True)
    with pytest.raises(ValidationError):  # no change from a card
        deliver(order.pk, payments=(PaymentInput(kind="card", currency_code="EGP", amount="300",
                                                 terminal_id=shop.terminal.pk),))
    order = deliver(order.pk, payments=(PaymentInput(
        kind="card", currency_code="EGP", amount="250", terminal_id=shop.terminal.pk),))
    assert order.state == "delivered" and order.paid_amount == Decimal("250")
    with pytest.raises(DomainError):  # closed
        cancel_repair(order.pk)
    assert balanced()


def test_on_account_and_custom_orders(shop):
    order = take_in(shop, deposit="100", kind=RepairKind.CUSTOM)
    ready(order)
    order = deliver(order.pk, payments=(shop.cash("50"),), on_account=True)
    assert order.balance_amount == Decimal("100")  # 250 − 100 held − 50 paid
    assert party_balance(shop.customer, Account.objects.get(role="customers")) == {
        "EGP": Decimal("100")}
    assert balanced()


@pytest.mark.parametrize("method", [RefundMethod.CASH, RefundMethod.CUSTOMER_CREDIT])
def test_cancel_returns_the_deposit(shop, method):
    order = take_in(shop, deposit="100")
    add_deposit(order.pk, shop.cash("50"))
    order = cancel_repair(order.pk, refund_method=method, reason="Changed her mind")
    assert order.state == "cancelled" and held(shop) == 0
    if method == RefundMethod.CASH:
        assert cash(shop) == 0
    else:
        assert cash(shop) == 150
        assert party_balance(shop.customer, Account.objects.get(role="customers")) == {
            "EGP": Decimal("-150")}
    assert balanced()


def test_rules(shop):
    with pytest.raises(ValidationError):  # nobody
        create_repair(RepairInput(branch_id=shop.branch.pk,
                                  lines=(RepairLineInput("Ring"),)))
    with pytest.raises(ValidationError):  # nothing
        create_repair(RepairInput(branch_id=shop.branch.pk, customer_id=shop.customer.pk,
                                  lines=()))
    with pytest.raises(ValidationError):
        create_repair(RepairInput(branch_id=shop.branch.pk, customer_id=shop.customer.pk,
                                  lines=(RepairLineInput("Ring", weight_in_g="-1"),)))
    order = take_in(shop)
    with pytest.raises(ValidationError):  # a customer is not a workshop
        send_to_workshop(order.pk, shop.customer.pk)
    with pytest.raises(ValidationError):  # every piece needs its weight out
        mark_ready(order.pk, (ReadyLineInput(order.lines.first().pk, "4"),))


def test_api_and_pages(tenant_a, shop):
    client = login(tenant_a, language="en")
    made = client.post("/api/v1/repairs/", {
        "branch": shop.branch.pk, "customer": shop.customer.pk, "kind": "repair",
        "promised_on": "2099-01-01",
        "lines": [{"description": "Ring: resize", "karat": shop.k21.pk, "weight_in_g": "4.5",
                   "charge": "150"}],
        "deposit": {"kind": "cash", "currency": "EGP", "amount": "50"}},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="rp1")
    assert made.status_code == 201, made.content
    repair = made.json()
    assert (repair["bag_number"], repair["deposit_amount"]) == (1, "50.00")
    url = f"/api/v1/repairs/{repair['id']}/"
    page = client.get(f"/repairs/{repair['id']}/").content.decode()
    assert "Send to a workshop" in page and "Mark ready" in page and "Deliver" not in page

    sent = client.post(url + "send/", {"workshop": shop.workshop.pk},
                       content_type="application/json", HTTP_IDEMPOTENCY_KEY="rp1s")
    assert sent.json()["state"] == "at_workshop"
    with tenant_context(tenant_a.id):
        line = RepairOrder.objects.get(pk=repair["id"]).lines.get()
    ready_ = client.post(url + "ready/", {
        "lines": [{"line": line.pk, "weight_out_g": "4.48", "charge": "160"}],
        "labour_amount": "70"}, content_type="application/json", HTTP_IDEMPOTENCY_KEY="rp1r")
    assert ready_.status_code == 200, ready_.content
    assert ready_.json()["state"] == "ready"
    assert "Repairs ready for pickup" in client.get("/").content.decode()
    page = client.get(f"/repairs/{repair['id']}/").content.decode()
    assert "Deliver and take payment" in page and "110.00" in page  # 160 − 50 held

    done = client.post(url + "deliver/", {"payments": [
        {"kind": "cash", "currency": "EGP", "amount": "110"}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="rp1d")
    assert done.status_code == 200, done.content
    assert done.json()["state"] == "delivered"
    for path in ("/repairs/", "/repairs/?state=delivered", "/repairs/new/",
                 f"/repairs/new/?customer={shop.customer.pk}",
                 f"/repairs/?customer={shop.customer.pk}&state=all", "/repairs/?q=1",
                 f"/repairs/{repair['id']}/", f"/print/repair/{repair['id']}/"):
        assert client.get(path).status_code == 200, path
    customer_page = client.get(f"/customers/{shop.customer.pk}/").content.decode()
    assert f"/repairs/new/?customer={shop.customer.pk}" in customer_page
