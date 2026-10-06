"""In-house production orders: our own gold and scrap made into new goods in our own shop."""

from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.hr.services import EmployeeInput, create_employee, update_employee
from apps.inventory.models import Item, ItemStatus, LotBalance, MovementType, StockMovement
from apps.inventory.services import scrap_category
from apps.manufacturing.services import (
    DOC_TYPE,
    IssueInput,
    IssueLineInput,
    ReceiptInput,
    cancel_receipt,
    cancel_work_order,
    issue_work_order,
    receive_work_order,
)
from apps.manufacturing.tests.test_work_orders import balanced, rings, roles, scrap
from conftest import login

pytestmark = pytest.mark.django_db


def make(stock, grams="10", craftsman=None, category=None, karat=None):
    return issue_work_order(IssueInput(
        branch_id=stock.branch.pk, workshop_id=None, in_house=True, craftsman_id=craftsman,
        lines=(IssueLineInput(category_id=(category or stock.chain).pk,
                              karat_id=(karat or stock.k21).pk, gross_weight_g=grams),)))


def movements(order):
    return set(StockMovement.objects.filter(document_type=DOC_TYPE, document_id=order.pk)
               .values_list("movement_type", flat=True))


def test_make_pieces_in_house(stock):
    hany = create_employee(EmployeeInput(name="Hany", job_title="Goldsmith"))
    order = make(stock, craftsman=hany.pk)
    assert order.in_house and order.craftsman == hany and "-PRD-" in order.number
    assert stock.lot(stock.branch).gross_weight_g == Decimal("10.000")
    assert movements(order) == {MovementType.PRODUCTION_CONSUME}
    ledger = roles()
    # The gold (8.75 fine g) and the making cost it carried sit in production, not at a workshop.
    assert ledger["inventory_in_production"].metals["XAU"] == Decimal("8.75")
    assert "workshops" not in ledger and "inventory_at_workshop" not in ledger
    assert balanced()

    order = receive_work_order(order.pk, ReceiptInput(
        lines=(rings(stock, "4", "5"), scrap(stock, "1"))))
    assert order.loss_fine_weight_g == Decimal("1.125") and order.labour_amount == Decimal("540")
    assert movements(order) == {MovementType.PRODUCTION_CONSUME, MovementType.PRODUCTION_OUTPUT}
    ledger = roles()
    assert "inventory_in_production" not in ledger
    assert ledger["metal_loss"].metals["XAU"] == Decimal("1.125")
    # Our craftsman's labour goes into the cost of the rings, against labour absorbed.
    assert ledger["labour_absorbed"].credit == Decimal("540")
    made = sorted(Item.objects.filter(supplier__isnull=True, cost_amount__gt=0,
                                      category=stock.rings, karat=stock.k18),
                  key=lambda item: item.gross_weight_g)
    assert [i.cost_amount for i in made] == [Decimal("684.44"), Decimal("855.56")]
    assert balanced()

    cancel_receipt(order.pk)
    for item in made:
        item.refresh_from_db()
    assert {i.status for i in made} == {ItemStatus.VOIDED}
    cancel_work_order(order.pk)
    assert stock.lot(stock.branch).gross_weight_g == Decimal("20.000")
    ledger = roles()
    assert "inventory_in_production" not in ledger and "labour_absorbed" not in ledger
    assert balanced()


def test_melt_scrap_into_new_pieces(stock):
    first = make(stock, "4")  # 4 g of 21K chain melted down to scrap
    receive_work_order(first.pk, ReceiptInput(lines=(scrap(stock, "4"),)))
    assert LotBalance.objects.get(lot__category=scrap_category()).gross_weight_g == Decimal("4")

    # The 21K scrap (3.5 fine g) becomes one 18K ring of 4.5 g (3.375 fine g).
    order = make(stock, "4", category=scrap_category())
    assert order.carried_cost == 0  # scrap carries no making cost
    order = receive_work_order(order.pk, ReceiptInput(lines=(rings(stock, "4.5", labour="40"),)))
    assert order.loss_fine_weight_g == Decimal("0.125")
    assert LotBalance.objects.get(lot__category=scrap_category()).gross_weight_g == 0
    ring = Item.objects.filter(category=stock.rings, karat=stock.k18,
                               gross_weight_g=Decimal("4.5")).get()
    assert ring.cost_amount == Decimal("180")  # labour only: 40 × 4.5 g
    assert balanced()


def test_rules(stock):
    hany = create_employee(EmployeeInput(name="Hany"))
    update_employee(hany.pk, EmployeeInput(name="Hany", is_active=False))
    with pytest.raises(ValidationError):  # an employee who left
        make(stock, craftsman=hany.pk)
    with pytest.raises(ValidationError):  # without "in house" a workshop is required
        issue_work_order(IssueInput(branch_id=stock.branch.pk, workshop_id=None, lines=(
            IssueLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                           gross_weight_g="1"),)))


def test_api_and_pages(tenant_a, stock):
    hany = create_employee(EmployeeInput(name="Hany", job_title="Goldsmith"))
    client = login(tenant_a, language="en")
    made = client.post("/api/v1/manufacturing/work-orders/", {
        "branch": stock.branch.pk, "in_house": True, "craftsman": hany.pk,
        "lines": [{"category": stock.chain.pk, "karat": stock.k21.pk, "gross_weight_g": "10"}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="prd1")
    assert made.status_code == 201, made.content
    body = made.json()
    assert (body["in_house"], body["workshop"], body["craftsman_name"]) == (True, None, "Hany")
    order = body["id"]

    page = client.get(f"/production/{order}/").content.decode()
    assert "Record what was made" in page and "In our own shop" in page
    assert "Production in progress" in client.get("/").content.decode()
    listing = client.get("/production/").content.decode()
    assert body["number"] in listing
    assert body["number"] not in client.get("/manufacturing/").content.decode()

    received = client.post(f"/api/v1/manufacturing/work-orders/{order}/receive/", {"lines": [
        {"category": stock.rings.pk, "karat": stock.k18.pk, "piece_weights": ["4", "5"],
         "labour_rate": "60", "list_making_rate": "250"}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="prd1r")
    assert received.status_code == 200, received.content
    page = client.get(f"/production/{order}/").content.decode()
    assert "Goods made" in page and "Labour added to cost" in page and "Pay workshop" not in page
    for path in ("/production/", "/production/?state=received", "/production/?state=all",
                 f"/production/?craftsman={hany.pk}&state=all", "/production/new/"):
        assert client.get(path).status_code == 200, path
