"""Casting gold into bars (legacy GHSHH15 / GHSHH24 سبك): each bar comes back with its own weight,
fineness and the caster's serial; the loss is worked out from the bars' own fine gold, and the
casting can be charged as a fixed amount."""

from decimal import Decimal

import pytest

from apps.catalog.models import Karat, ProductFamily, Tracking
from apps.catalog.services import CreateItemCategoryCommand, create_item_category
from apps.core.errors import ValidationError
from apps.inventory.models import Item
from apps.ledger.selectors import party_balance
from apps.manufacturing.services import ReceiptInput, ReceiptLineInput, receive_work_order
from apps.manufacturing.tests.test_work_orders import send
from apps.printing.labels import field_text
from conftest import login

pytestmark = pytest.mark.django_db


def _bars_category():
    return create_item_category(CreateItemCategoryCommand(
        code="7010", name="Cast bars", product_family=ProductFamily.BULLION_COIN,
        tracking=Tracking.SERIALIZED, barcode_prefix=7010))


def _bars(stock, *bars, fee="300"):
    return ReceiptLineInput(category_id=stock.bars.pk, karat_id=Karat.objects.get(code=24).pk,
                            bars=bars, labour_amount=fee)


def test_bars_with_their_own_fineness_and_serial(stock):
    stock.bars = _bars_category()
    order = send(stock)  # 10 g of 21K = 8.75 fine
    with pytest.raises(ValidationError):  # the same serial twice
        receive_work_order(order.pk, ReceiptInput(lines=(_bars(
            stock, {"weight_g": "4", "fineness": "995", "serial": "A1"},
            {"weight_g": "4", "fineness": "995", "serial": "a1"}),)))
    with pytest.raises(ValidationError):
        receive_work_order(order.pk, ReceiptInput(lines=(_bars(
            stock, {"weight_g": "4", "fineness": "1200"}),)))

    received = receive_work_order(order.pk, ReceiptInput(lines=(_bars(
        stock, {"weight_g": "5.000", "fineness": "999.9", "serial": "EG-1001"},
        {"weight_g": "3.700", "fineness": "995", "serial": "EG-1002"}),)))
    # 5 × 0.9999 + 3.7 × 0.995 = 4.9995 + 3.6815 = 8.6810 fine; 0.0690 lost.
    assert received.received_fine_weight_g == Decimal("8.6810")
    assert received.loss_fine_weight_g == Decimal("0.0690")
    assert received.labour_amount == Decimal("300.00")
    assert party_balance(stock.workshop)["EGP"] == Decimal("-300.00")  # the fee is owed
    first = Item.objects.get(external_code="EG-1001")
    assert first.attributes == {"fineness": "999.9"} and first.fine_weight_g == Decimal("4.9995")
    second = Item.objects.get(external_code="EG-1002")
    assert second.fine_weight_g == Decimal("3.6815") and second.category == stock.bars
    assert (field_text("fineness", second), field_text("serial", second)) == ("995.0", "EG-1002")


def test_receive_bars_through_the_api(tenant_a, stock):
    stock.bars = _bars_category()
    order = send(stock)
    client = login(tenant_a, language="en")
    page = client.get(f"/manufacturing/{order.pk}/").content.decode()
    assert 'data-kind="bars"' in page and "Fineness (per mille)" in page
    url = f"/api/v1/manufacturing/work-orders/{order.pk}/"
    body = {"lines": [{"category": stock.bars.pk, "karat": Karat.objects.get(code=24).pk,
                       "bars": [{"weight_g": "8.7", "fineness": "999.9", "serial": "X9"}],
                       "labour_amount": "150"}]}
    preview = client.post(url + "preview/", body, content_type="application/json")
    assert preview.status_code == 200, preview.content
    made = client.post(url + "receive/", body, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY="bars1")
    assert made.status_code == 200, made.content
    bar = Item.objects.get(external_code="X9")
    page = client.get(f"/stock/items/{bar.pk}/").content.decode()
    assert "999.9" in page and "X9" in page
    assert "X9" in client.get(f"/manufacturing/{order.pk}/").content.decode()
