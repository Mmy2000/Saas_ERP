"""The printed work order (legacy GHSH14): what went to the workshop and what came back."""

import pytest

from apps.manufacturing.services import ReceiptInput, receive_work_order
from apps.manufacturing.tests.test_work_orders import rings, send
from conftest import login

pytestmark = pytest.mark.django_db


def test_print_a_work_order(tenant_a, stock):
    order = send(stock)
    client = login(tenant_a, language="en")
    html = client.get(f"/print/work_order/{order.pk}/").content.decode()
    assert "Work order" in html and order.number in html and "Atef Workshop" in html
    assert "Sent" in html and "8.7500" in html and "Loss (fine g)" not in html
    assert f"/print/work_order/{order.pk}/" in client.get(
        f"/manufacturing/{order.pk}/").content.decode()

    receive_work_order(order.pk, ReceiptInput(lines=(rings(stock, "4", "5.6"),)))
    html = client.get(f"/print/work_order/{order.pk}/").content.decode()
    assert "Back" in html and "Fine gold back" in html and "Labour" in html
    assert client.get("/settings/documents/work_order/").status_code == 200
