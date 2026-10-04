from datetime import date
from decimal import Decimal

import pytest

from apps.catalog.models import ProductFamily, Tracking
from apps.catalog.services import CreateItemCategoryCommand, create_item_category
from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.inventory.models import (
    Item,
    ItemStatus,
    LotBalance,
    StockLot,
    StocktakeResult,
)
from apps.inventory.stocktakes import (
    cancel_stocktake,
    post_stocktake,
    scan,
    start_stocktake,
    summary,
    undo_scan,
    weigh_lot,
)
from apps.inventory.transfers import (
    TransferInput,
    TransferLineInput,
    receive_transfer,
    send_transfer,
    void_transfer,
)
from apps.ledger.selectors import trial_balance
from apps.org.services import BranchInput, create_branch
from apps.purchasing.services import InvoiceInput, LineInput, post_invoice, save_draft
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from conftest import login

pytestmark = pytest.mark.django_db


class Stock(Shop):
    """Two 18K rings and 20 g of 21K chain at branch 1, and a second branch."""

    def __init__(self):
        super().__init__()
        self.chain = create_item_category(CreateItemCategoryCommand(
            code="2020", name="Chain", product_family=ProductFamily.GOLD,
            tracking=Tracking.BULK))
        post_invoice(save_draft(InvoiceInput(
            supplier_id=self.ring_a.supplier_id, branch_id=self.branch.pk,
            business_date=date(2026, 9, 1), currency_code="EGP",
            lines=(LineInput(category_id=self.chain.pk, karat_id=self.k21.pk, qty=4,
                             gross_weight_g="20", making_cost_rate="100"),))).pk)
        self.other = create_branch(BranchInput(code=2, name="Maadi"))

    def lot(self, branch):
        return LotBalance.objects.get(lot__category=self.chain, lot__branch=branch)


@pytest.fixture
def stock(tenant_a):
    with tenant_context(tenant_a.id):
        yield Stock()


def branch_rows(branch):
    return {(r.account.role, code): q for r in trial_balance(branch_ids=[branch.pk]).rows
            for code, q in r.metals.items()}


def send(stock, *lines):
    return send_transfer(TransferInput(from_branch_id=stock.branch.pk,
                                       to_branch_id=stock.other.pk, lines=tuple(lines)))


class TestTransfers:
    def test_send_and_receive(self, stock):
        transfer = send(stock, TransferLineInput(barcode=stock.ring_a.barcode),
                        TransferLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                                          gross_weight_g="5", qty=1))
        assert transfer.number == "01-TF-2026-000001" and transfer.in_transit
        assert transfer.total_qty == 2 and transfer.total_gross_weight_g == Decimal("10.000")
        stock.ring_a.refresh_from_db()
        assert (stock.ring_a.status, stock.ring_a.branch) == (ItemStatus.IN_TRANSIT, stock.other)
        assert stock.lot(stock.branch).gross_weight_g == Decimal("15.000")
        # The gold is in branch clearing at the sending branch until it arrives.
        clearing = branch_rows(stock.branch)[("branch_clearing", "XAU")]
        assert clearing == Decimal("3.75") + Decimal("4.375")
        with pytest.raises(ValidationError):  # in transit: cannot be sold at either branch
            post_sale(stock.sale(stock.line(stock.ring_a), payments=[stock.cash(RING_A_TOTAL)]))

        receive_transfer(transfer.pk)
        stock.ring_a.refresh_from_db()
        assert stock.ring_a.status == ItemStatus.IN_STOCK
        assert stock.lot(stock.other).gross_weight_g == Decimal("5.000")
        assert ("branch_clearing", "XAU") not in {
            k for k, v in {**branch_rows(stock.branch), **branch_rows(stock.other)}.items()
            if v == 0}
        tb = trial_balance()
        assert tb.total_debit == tb.total_credit
        assert not any(r.account.role == "branch_clearing" for r in tb.rows)
        with pytest.raises(DomainError):
            receive_transfer(transfer.pk)

    def test_cancel_in_transit(self, stock):
        transfer = send(stock, TransferLineInput(barcode=stock.ring_a.barcode),
                        TransferLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                                          gross_weight_g="20", qty=4))
        void_transfer(transfer.pk)
        stock.ring_a.refresh_from_db()
        assert (stock.ring_a.status, stock.ring_a.branch) == (ItemStatus.IN_STOCK, stock.branch)
        assert stock.lot(stock.branch).gross_weight_g == Decimal("20.000")
        transfer.refresh_from_db()
        assert transfer.status == DocStatus.VOIDED
        assert not any(r.account.role == "branch_clearing" for r in trial_balance().rows)

    def test_rules(self, stock):
        with pytest.raises(ValidationError):  # nothing to send
            send(stock)
        with pytest.raises(ValidationError) as exc:
            send(stock, TransferLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                                          gross_weight_g="25"))
        assert "lines" in exc.value.fields
        with pytest.raises(ValidationError):
            send(stock, TransferLineInput(barcode=stock.ring_a.barcode),
                 TransferLineInput(barcode=stock.ring_a.barcode))
        with pytest.raises(ValidationError):
            send_transfer(TransferInput(from_branch_id=stock.branch.pk,
                                        to_branch_id=stock.branch.pk,
                                        lines=(TransferLineInput(barcode="x"),)))


class TestStocktake:
    def test_count_and_post(self, stock):
        take = start_stocktake(stock.branch.pk)
        assert take.number == "01-SK-2026-000001"
        assert summary(take).expected == 2 and summary(take).lots == 1
        with pytest.raises(DomainError):  # one open count per branch
            start_stocktake(stock.branch.pk)

        assert scan(take.pk, stock.ring_a.barcode).outcome == "counted"
        assert scan(take.pk, stock.ring_a.barcode).outcome == "already"
        stray = scan(take.pk, "NOPE-1")
        assert stray.outcome == "unexpected" and stray.line.item is None
        undo_scan(take.pk, stray.line.pk)
        lot_line = take.lot_lines.get()
        weigh_lot(take.pk, lot_line.pk, "19.5", 4)
        assert summary(take).counted == 1 and summary(take).lots_weighed == 1

        post_stocktake(take.pk)
        stock.ring_b.refresh_from_db()
        assert stock.ring_b.status == ItemStatus.MISSING
        assert take.lines.get(item=stock.ring_b).result == StocktakeResult.MISSING
        assert stock.lot(stock.branch).gross_weight_g == Decimal("19.500")
        loss = next(r for r in trial_balance().rows if r.account.role == "metal_loss")
        assert loss.metals["XAU"] == Decimal("4.5") + Decimal("0.4375")  # ring_b 6g 18K + 0.5g 21K
        tb = trial_balance()
        assert tb.total_debit == tb.total_credit

        # Found again at the next count: back in stock as a gain.
        again = start_stocktake(stock.branch.pk)
        assert scan(again.pk, stock.ring_b.barcode).outcome == "unexpected"
        post_stocktake(again.pk)
        stock.ring_b.refresh_from_db()
        assert stock.ring_b.status == ItemStatus.IN_STOCK
        assert again.lines.get(item=stock.ring_b).adjusted

    def test_pieces_sold_during_the_count_are_left_alone(self, stock):
        take = start_stocktake(stock.branch.pk, category_id=stock.ring_a.category_id)
        assert summary(take).lots == 0
        post_sale(stock.sale(stock.line(stock.ring_a), payments=[stock.cash(RING_A_TOTAL)]))
        scan(take.pk, stock.ring_b.barcode)
        post_stocktake(take.pk)
        line = take.lines.get(item=stock.ring_a)
        assert line.result == StocktakeResult.LEFT and not line.adjusted
        assert Item.objects.get(pk=stock.ring_a.pk).status == ItemStatus.SOLD

    def test_cancel(self, stock):
        take = start_stocktake(stock.branch.pk)
        cancel_stocktake(take.pk)
        with pytest.raises(DomainError):
            scan(take.pk, stock.ring_a.barcode)
        start_stocktake(stock.branch.pk)  # a new one can start
        assert StockLot.objects.count() == 1


def test_api_and_pages(tenant_a, stock):
    client = login(tenant_a)
    body = {"from_branch": stock.branch.pk, "to_branch": stock.other.pk,
            "lines": [{"barcode": stock.ring_a.barcode}]}
    sent = client.post("/api/v1/inventory/transfers/", body, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY="tf1")
    assert sent.status_code == 201, sent.content
    received = client.post(f"/api/v1/inventory/transfers/{sent.json()['id']}/receive/",
                           content_type="application/json", HTTP_IDEMPOTENCY_KEY="tf1r")
    assert received.json()["in_transit"] is False
    take = client.post("/api/v1/inventory/stocktakes/", {"branch": stock.branch.pk},
                       content_type="application/json")
    assert take.status_code == 201, take.content
    take_id = take.json()["id"]
    scanned = client.post(f"/api/v1/inventory/stocktakes/{take_id}/scan/",
                          {"barcode": stock.ring_b.barcode}, content_type="application/json")
    assert scanned.json()["outcome"] == "counted" and scanned.json()["summary"]["counted"] == 1
    for path in ("/stock/transfers/", "/stock/transfers/new/",
                 f"/stock/transfers/{sent.json()['id']}/", "/stock/stocktakes/",
                 f"/print/transfer/{sent.json()['id']}/",
                 "/stock/stocktakes/new/", f"/stock/stocktakes/{take_id}/",
                 f"/stock/items/{stock.ring_a.pk}/"):
        assert client.get(path).status_code == 200, path
    posted = client.post(f"/api/v1/inventory/stocktakes/{take_id}/post/",
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="sk1")
    assert posted.json()["status"] == "posted"
    assert client.get(f"/stock/stocktakes/{take_id}/").status_code == 200
