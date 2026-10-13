"""The supplier's own invoice totals checked against the lines (Gweb only showed a coding
difference, GHS14)."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.purchasing.services import InvoiceInput, save_draft, stated_check, totals
from apps.purchasing.tests.test_purchasing import DAY, shop  # noqa: F401
from conftest import login

pytestmark = pytest.mark.django_db


def draft(shop, **stated):
    return save_draft(InvoiceInput(
        supplier_id=shop.supplier.pk, branch_id=shop.branch.pk, business_date=DAY,
        currency_code="EGP", lines=(shop.rings_line("5.000", "6.000"),), **stated))


def test_stated_totals(shop):
    invoice = draft(shop, stated_pieces=2, stated_weight_g="11.500", stated_making_amount="")
    assert invoice.stated_weight_g == Decimal("11.500") and invoice.stated_making_amount is None
    with translation.override("en"):
        rows = stated_check(invoice, totals(invoice))
    assert [(row["label"], row["difference"]) for row in rows] == [
        ("Pieces", 0), ("Total weight", Decimal("-0.500"))]
    with pytest.raises(ValidationError):
        draft(shop, stated_weight_g="-1")
    with pytest.raises(ValidationError):
        draft(shop, stated_fine_g="lots")


def test_detail_page(tenant_a, shop):
    with tenant_context(tenant_a.id):
        invoice = draft(shop, stated_pieces=2, stated_weight_g="11")
    client = login(tenant_a, language="en")
    page = client.get(f"/purchasing/{invoice.pk}/").content.decode()
    assert "Against their invoice" in page and "Matches" in page
    listing = client.get("/purchasing/")  # an invoice on the page and the totals together
    assert listing.status_code == 200 and invoice.number is None
    form = client.get(f"/purchasing/{invoice.pk}/edit/").content.decode()
    assert "Totals on their invoice" in form and 'value="11.000"' in form


def test_coding_differences_report(shop):
    from apps.org.models import Branch
    from apps.reports.registry import REPORTS

    draft(shop, stated_pieces=2, stated_weight_g="11")  # matches
    differs = draft(shop, stated_weight_g="12.250")
    with translation.override("en"):
        report = REPORTS["coding_differences"]()
        [table] = report.run(report.parse({"date_from": str(DAY)}, Branch.objects.all()), None)
        [row] = table.rows
        assert row["weight"] == Decimal("-1.250") and "pieces" not in row
        assert row["_urls"]["number"] == f"/purchasing/{differs.pk}/"
        [every] = report.run(report.parse({"date_from": str(DAY), "show": "all"},
                                          Branch.objects.all()), None)
        assert len(every.rows) == 2
