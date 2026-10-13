"""A diamond piece's model / style, and the parcel stones bought and sold report."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.tenancy import tenant_context
from apps.diamonds import parcels, services
from apps.diamonds.tests.test_diamonds import SOLITAIRE, shop  # noqa: F401
from apps.diamonds.tests.test_parcels import melee  # noqa: F401
from apps.inventory.models import Item
from apps.org.models import Branch
from apps.printing.labels import field_text
from apps.reports.registry import REPORTS
from conftest import login

pytestmark = pytest.mark.django_db


def receive_model(shop, model):
    invoice = services.receive(services.ReceiveInput(
        supplier_id=shop.dealer.pk, branch_id=shop.branch.pk, currency_code="EGP",
        pieces=(services.PieceInput(category_id=shop.mountings.pk, karat_id=shop.k18.pk,
                                    gross_weight_g="5.200", making_cost_rate="100",
                                    stone_cost="30000", label_price="60000",
                                    stones=SOLITAIRE, model_code=model),)))
    return Item.objects.get(pk=invoice.lines.get().pieces.get().item_id)


def test_model_code(tenant_a, shop):
    with tenant_context(tenant_a.id):
        ring = receive_model(shop, " SOL-118 ")
        assert ring.model_code == "SOL-118" and field_text("model", ring) == "SOL-118"
        services.update_item(ring.pk, stones=SOLITAIRE, model_code="SOL-120")
        ring.refresh_from_db()
        assert ring.model_code == "SOL-120"
    client = login(tenant_a, language="en")
    found = client.get("/diamonds/", {"q": "sol-120"}).content.decode()
    assert ring.barcode in found and "SOL-120" in found


def test_parcel_report(tenant_a, melee):
    parcels.deal(parcels.DealInput(parcel_id=melee.parcel.pk, kind="sell", carats="0.5",
                                   count=25, price_per_ct="20000", payment="cash",
                                   party_name="Walk-in"))
    parcels.deal(parcels.DealInput(parcel_id=melee.parcel.pk, kind="return", carats="0.5",
                                   price_per_ct="", payment="account",
                                   party_id=melee.dealer.pk))
    with translation.override("en"):
        report = REPORTS["diamond_parcels"]()
        tables = {str(t.title): t for t in report.run(report.parse({}, Branch.objects.all()),
                                                       None)}
    [row] = tables["Per parcel"].rows
    assert (row["bought_ct"], row["bought"]) == (Decimal("3.000"), Decimal("33000.00"))
    assert (row["sold"], row["cost"], row["margin"]) == (
        Decimal("10000.00"), Decimal("5500.00"), Decimal("4500.00"))
    assert row["returned_ct"] == Decimal("0.500")
    assert len(tables["Deals"].rows) == 4
    client = login(tenant_a, language="en")
    assert client.get("/reports/diamond_parcels/").status_code == 200


def test_stock_by_branch_and_settings_report(tenant_a, shop):
    with tenant_context(tenant_a.id):
        receive_model(shop, "")
        with translation.override("en"):
            report = REPORTS["diamond_stock"]()
            tables = {str(t.title): t for t in report.run(
                report.parse({}, Branch.objects.all()), None)}
            [branch] = tables["By branch"].rows
            assert branch["pieces"] == 1 and branch["carats"] == Decimal("0.740")
            settings = REPORTS["diamond_settings"]()
            [table] = settings.run(settings.parse({}, Branch.objects.all()), None)
            assert table.rows == []
    client = login(tenant_a, language="en")
    for code in ("diamond_settings", "diamond_sales"):
        assert client.get(f"/reports/{code}/").status_code == 200, code
