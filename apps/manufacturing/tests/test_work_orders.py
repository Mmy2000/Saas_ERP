from decimal import Decimal

import pytest

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.inventory.models import Item, ItemStatus, LotBalance
from apps.inventory.services import scrap_category
from apps.ledger.selectors import party_balance, trial_balance
from apps.manufacturing.services import (
    IssueInput,
    IssueLineInput,
    ReceiptInput,
    ReceiptLineInput,
    cancel_receipt,
    cancel_work_order,
    issue_work_order,
    plan_receipt,
    receive_work_order,
)
from apps.parties.services import PartyData, create_customer
from apps.sales.services import post_sale
from conftest import login

pytestmark = pytest.mark.django_db

# The chain lot holds 20 g of 21K with a making cost of 100/g (the `stock` fixture).


def send(stock, grams="10", workshop=None):
    return issue_work_order(IssueInput(
        branch_id=stock.branch.pk, workshop_id=(workshop or stock.workshop).pk,
        lines=(IssueLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                              gross_weight_g=grams),)))


def rings(stock, *weights, labour="60"):
    return ReceiptLineInput(category_id=stock.rings.pk, karat_id=stock.k18.pk,
                            piece_weights=weights, labour_rate=labour, list_making_rate="250")


def scrap(stock, grams):
    return ReceiptLineInput(category_id=scrap_category().pk, karat_id=stock.k21.pk,
                            gross_weight_g=grams)


def roles():
    return {r.account.role: r for r in trial_balance().rows}


def balanced():
    tb = trial_balance()
    return tb.total_debit == tb.total_credit


def test_send_receive_with_loss(stock):
    order = send(stock)
    assert order.number == "01-WO-2026-000001" and order.at_workshop
    assert (order.issued_fine_weight_g, order.carried_cost) == (Decimal("8.75"), Decimal("1000"))
    assert stock.lot(stock.branch).gross_weight_g == Decimal("10.000")
    assert party_balance(stock.workshop) == {"XAU": Decimal("8.75")}  # they hold our gold
    assert roles()["inventory_at_workshop"].debit == Decimal("1000")
    assert balanced()

    # Two 18K rings (4 g and 5 g = 6.75 fine g) and 1 g of 21K scrap (0.875) come back.
    order = receive_work_order(order.pk, ReceiptInput(
        lines=(rings(stock, "4", "5"), scrap(stock, "1"))))
    assert order.state == "received"
    assert order.received_fine_weight_g == Decimal("7.625")
    assert (order.loss_fine_weight_g, order.gain_fine_weight_g) == (Decimal("1.125"), 0)
    assert order.labour_amount == Decimal("540.00")
    # The gold is settled; the labour is owed to the workshop.
    assert party_balance(stock.workshop) == {"EGP": Decimal("-540")}
    ledger = roles()
    assert ledger["metal_loss"].metals["XAU"] == Decimal("1.125")
    assert "inventory_at_workshop" not in ledger
    assert balanced()

    # Labour and the making cost carried in the chain become the cost of the new rings.
    made = sorted(Item.objects.filter(supplier=stock.workshop), key=lambda i: i.gross_weight_g)
    assert [i.status for i in made] == [ItemStatus.IN_STOCK] * 2
    assert [i.cost_amount for i in made] == [Decimal("684.44"), Decimal("855.56")]
    assert made[0].list_making_rate == Decimal("250")
    assert LotBalance.objects.get(lot__category=scrap_category()).gross_weight_g == Decimal("1")

    # Undo the receipt, then the order itself: everything is as it was.
    cancel_receipt(order.pk)
    order.refresh_from_db()
    assert order.at_workshop and not order.receipt_lines.exists()
    assert party_balance(stock.workshop) == {"XAU": Decimal("8.75")}
    assert all(i.status == ItemStatus.VOIDED for i in Item.objects.filter(
        supplier=stock.workshop))
    cancel_work_order(order.pk)
    assert stock.lot(stock.branch).gross_weight_g == Decimal("20.000")
    assert stock.lot(stock.branch).cost_amount == Decimal("2000")
    assert party_balance(stock.workshop) == {}
    assert balanced()


def test_gain_must_be_confirmed(stock):
    order = send(stock, "4")  # 3.5 fine g
    back = ReceiptInput(lines=(rings(stock, "5", labour="0"),))  # 3.75 fine g
    with pytest.raises(DomainError):
        receive_work_order(order.pk, back)
    plan = plan_receipt(order, ReceiptInput(lines=back.lines, accept_gain=True))
    assert plan.gain == {"gold": Decimal("0.25")} and plan.loss == {}
    order = receive_work_order(order.pk, ReceiptInput(lines=back.lines, accept_gain=True))
    assert order.gain_fine_weight_g == Decimal("0.25")
    assert roles()["metal_gain"].metals["XAU"] == Decimal("-0.25")
    assert party_balance(stock.workshop) == {}
    assert balanced()


def test_only_scrap_back_writes_off_the_carried_cost(stock):
    order = send(stock, "4")  # carried making cost 400
    receive_work_order(order.pk, ReceiptInput(lines=(scrap(stock, "4"),)))
    ledger = roles()
    assert ledger["metal_loss"].debit == Decimal("400") and "XAU" not in ledger["metal_loss"].metals
    assert "inventory_at_workshop" not in ledger
    assert balanced()


def test_rules(stock):
    with pytest.raises(ValidationError):  # a customer is not a workshop
        send(stock, workshop=create_customer(PartyData(name="Mona")))
    with pytest.raises(ValidationError):
        send(stock, "25")
    order = send(stock)
    with pytest.raises(ValidationError):  # pieces need a weight each
        receive_work_order(order.pk, ReceiptInput(lines=(rings(stock),)))
    with pytest.raises(ValidationError):
        receive_work_order(order.pk, ReceiptInput(lines=()))
    with pytest.raises(DomainError):
        cancel_receipt(order.pk)
    receive_work_order(order.pk, ReceiptInput(lines=(rings(stock, "4", "5"),)))
    with pytest.raises(DomainError):  # received orders are not cancelled, their receipt is
        cancel_work_order(order.pk)
    with pytest.raises(DomainError):
        receive_work_order(order.pk, ReceiptInput(lines=(rings(stock, "1"),)))
    # Once a new ring is sold the receipt can no longer be undone.
    ring = Item.objects.filter(supplier=stock.workshop).first()
    quote_total = (Decimal("3428.57") + 250) * ring.gross_weight_g
    post_sale(stock.sale(stock.line(ring), payments=[stock.cash(quote_total.quantize(
        Decimal("0.01")))]))
    with pytest.raises(DomainError):
        cancel_receipt(order.pk)


def test_api_and_pages(tenant_a, stock):
    client = login(tenant_a, language="en")
    made = client.post("/api/v1/parties/workshops/", {"name": "Delta Casting"},
                       content_type="application/json")
    assert made.status_code == 201, made.content
    workshop = made.json()["id"]
    sent = client.post("/api/v1/manufacturing/work-orders/", {
        "branch": stock.branch.pk, "workshop": workshop, "kind": "casting",
        "lines": [{"category": stock.chain.pk, "karat": stock.k21.pk, "gross_weight_g": "10"}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="wo1")
    assert sent.status_code == 201, sent.content
    order = sent.json()["id"]
    assert sent.json()["state"] == "at_workshop"

    page = client.get(f"/manufacturing/{order}/").content.decode()
    assert "Record what came back" in page and "Cancel order" in page
    assert "Work orders at workshops" in client.get("/").content.decode()

    lines = [{"category": stock.rings.pk, "karat": stock.k18.pk, "piece_weights": ["4", "5"],
              "labour_rate": "60", "list_making_rate": "250"},
             {"category": scrap_category().pk, "karat": stock.k21.pk, "gross_weight_g": "1"}]
    preview = client.post(f"/api/v1/manufacturing/work-orders/{order}/preview/",
                          {"lines": lines}, content_type="application/json")
    assert preview.status_code == 200, preview.content
    assert preview.json()["loss_fine_g"] == "1.1250" and preview.json()["labour"] == "540.00"
    received = client.post(f"/api/v1/manufacturing/work-orders/{order}/receive/",
                           {"lines": lines}, content_type="application/json",
                           HTTP_IDEMPOTENCY_KEY="wo1r")
    assert received.status_code == 200, received.content
    assert received.json()["state"] == "received"

    page = client.get(f"/manufacturing/{order}/").content.decode()
    assert "Received back" in page and "Cancel receipt" in page and "1.1250" in page
    for path in ("/manufacturing/", "/manufacturing/?state=received", "/manufacturing/new/",
                 f"/manufacturing/new/?workshop={workshop}", "/workshops/",
                 f"/workshops/{workshop}/", f"/workshops/{workshop}/statement/",
                 f"/manufacturing/?workshop={workshop}&state=all"):
        assert client.get(path).status_code == 200, path

    # Pay the labour in cash: nothing is owed either way.
    paid = client.post("/api/v1/settlements/", {
        "kind": "payment", "side": "workshop", "party": workshop, "branch": stock.branch.pk,
        "method": "bank_transfer", "currency": "EGP", "amount": "540",
        "bank_account": stock.bank.pk}, content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="wo1p")
    assert paid.status_code == 201, paid.content
    with tenant_context(tenant_a.id):
        from apps.parties.models import Party

        assert party_balance(Party.objects.get(pk=workshop)) == {}

    undone = client.post(f"/api/v1/manufacturing/work-orders/{order}/cancel-receipt/", {},
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="wo1c")
    assert undone.json()["state"] == "at_workshop"
