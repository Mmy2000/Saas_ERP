"""The diamond category card (legacy GHD51 كرت الصنف ماس): pieces and carats in and out with a
running balance."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.tenancy import tenant_context
from apps.diamonds.tests.test_diamonds import (
    receive_ring,
    shop,  # noqa: F401  (fixture)
)
from apps.org.models import Branch
from apps.reports.registry import REPORTS
from apps.sales.services import PaymentInput, post_sale
from conftest import login

pytestmark = pytest.mark.django_db


def test_card_counts_pieces_and_carats(tenant_a, shop):
    with tenant_context(tenant_a.id):
        sold = receive_ring(shop)
        receive_ring(shop, label="45000")
        post_sale(shop.sale(shop.line(sold), payments=[PaymentInput(
            kind="cash", currency_code="EGP", amount="60000")]))
        with translation.override("en"):
            report = REPORTS["diamond_card"]()
            tables = {str(t.title): t for t in report.run(
                report.parse({}, Branch.objects.all()), None)}
    [category] = tables["Opening and closing"].rows
    assert (category["in_n"], category["out_n"], category["close_n"]) == (2, 1, 1)
    assert category["in_ct"] == Decimal("1.480") and category["close_ct"] == Decimal("0.740")
    assert category["close_cost"] == sold.cost_amount + sold.stone_cost_amount
    lines = tables["Movements"].rows
    assert [row["n"] for row in lines] == [1, 1, -1]
    assert lines[-1]["balance"] == Decimal("0.740")
    client = login(tenant_a, language="en")
    page = client.get("/reports/diamond_card/").content.decode()
    assert "Diamond category card" in page and sold.barcode in page
