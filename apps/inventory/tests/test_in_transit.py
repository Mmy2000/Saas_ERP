"""Goods and cash sent between branches and not received yet (legacy الخزن الوسيطة)."""

import pytest

from apps.core.tenancy import tenant_context
from apps.inventory.tests.test_transfers_stocktakes import Stock
from apps.inventory.transfers import (
    TransferInput,
    TransferLineInput,
    receive_transfer,
    send_transfer,
)
from conftest import login

pytestmark = pytest.mark.django_db


def test_in_transit_page(tenant_a, make_member):
    with tenant_context(tenant_a.id):
        stock = Stock()
        from apps.treasury.tests.test_treasury import cash_sale

        cash_sale(stock)  # ring A sold for cash, part of which goes to Maadi
        transfer = send_transfer(TransferInput(
            from_branch_id=stock.branch.pk, to_branch_id=stock.other.pk,
            lines=(TransferLineInput(barcode=stock.ring_b.barcode),
                   TransferLineInput(category_id=stock.chain.pk, karat_id=stock.k21.pk,
                                     gross_weight_g="5", qty=1))))
        from apps.treasury.holders import default_cash_box
        from apps.treasury.models import CashBox
        from apps.treasury.services import TreasuryInput, post_treasury_document

        here = CashBox.objects.get()
        there = default_cash_box(stock.other, here.currency)
        cash = post_treasury_document(TreasuryInput(kind="transfer", source_box_id=here.pk,
                                                    dest_box_id=there.pk, amount="5000"))
    client = login(tenant_a, language="en")
    page = client.get("/stock/in-transit/").content.decode()
    assert transfer.number in page and cash.number in page
    assert "11.000" in page  # the 6 g ring and 5 g of chain on the way
    assert "21K" in page and "18K" in page and "5,000.00" in page
    # Only what comes to Maadi, or what Maadi sent (nothing).
    assert transfer.number in client.get(
        f"/stock/in-transit/?branch={stock.other.pk}&direction=in").content.decode()
    assert transfer.number not in client.get(
        f"/stock/in-transit/?branch={stock.other.pk}&direction=out").content.decode()

    with tenant_context(tenant_a.id):
        receive_transfer(transfer.pk)
    assert transfer.number not in client.get("/stock/in-transit/").content.decode()

    # A member who sees neither goods nor cash may not open it.
    from apps.iam.models import Role

    with tenant_context(tenant_a.id):
        Role.objects.create(code="nothing", name="Nothing")
    make_member(tenant_a, "nobody", "nothing")
    assert login(tenant_a, "nobody").get("/stock/in-transit/").status_code == 403
