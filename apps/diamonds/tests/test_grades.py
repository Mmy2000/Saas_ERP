"""Colour, clarity and cut lists (legacy GHZ12-GHZ14), stock filtered by them, and diamond
labels printing the stones' grading."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds import grades
from apps.diamonds.models import StoneGrade
from apps.diamonds.tests.test_diamonds import receive_ring, shop  # noqa: F401
from apps.printing.labels import field_text, zpl
from apps.printing.models import LabelTemplate
from conftest import login

pytestmark = pytest.mark.django_db
SMALL = ({"kind": "diamond", "shape": "round", "count": 1, "carat": "0.30", "color": "h",
          "clarity": "si1", "cut": "very good"},)


def test_lists_and_normalising(shop):
    lists = grades.choices()
    assert lists["color"][:3] == ["D", "E", "F"] and "VS1" in lists["clarity"]
    ring = receive_ring(shop, stones=SMALL)
    stone = ring.stones.get()
    assert (stone.color, stone.clarity, stone.cut) == ("H", "SI1", "Very Good")
    added = grades.add_grade("color", "Fancy Yellow")
    with pytest.raises(ValidationError):
        grades.add_grade("color", "fancy yellow")
    used = StoneGrade.objects.get(field="color", code="H")
    with pytest.raises(ValidationError):  # stones are graded H
        grades.update_grade(used.pk, code="H+")
    with pytest.raises(DomainError):
        grades.delete_grade(used.pk)
    grades.update_grade(used.pk, is_active=False)
    assert "H" not in grades.choices()["color"]
    grades.update_grade(added.pk, move=-1)
    added.refresh_from_db()
    assert grades.choices()["color"][-1] != "Fancy Yellow"
    grades.delete_grade(added.pk)


def test_stock_filters_and_labels(tenant_a, shop):
    with tenant_context(tenant_a.id):
        big = receive_ring(shop)  # a 0.50 ct D VS1 and 12 small stones
        small = receive_ring(shop, stones=SMALL, label="20000")
        with translation.override("en"):
            assert field_text("main_stone", big) == "0.5 ct D VS1"
            assert field_text("grade", big) == "D / VS1" and field_text("cut", big) == "Excellent"
            assert field_text("certificate", big) == "GIA 2141438171"
            assert field_text("all_stones", big) == "1×0.5 D VS1 + 12×0.02"
            assert field_text("main_stone", small) == "0.3 ct H SI1"
        template = LabelTemplate.objects.create(name="Diamond", width_mm=50, height_mm=25,
                                                fields=["code", "main_stone", "certificate"])
        assert "^FD0.5 ct D VS1^FS" in zpl(template, [big])
    client = login(tenant_a, language="en")
    every = client.get("/diamonds/").content.decode()
    assert big.barcode in every and small.barcode in every
    vs1 = client.get("/diamonds/", {"clarity": "VS1"}).content.decode()
    assert big.barcode in vs1 and small.barcode not in vs1
    light = client.get("/diamonds/", {"ct_max": "0.5"}).content.decode()
    assert small.barcode in light and big.barcode not in light  # big has 0.74 ct in all
    page = client.get("/diamonds/grades/").content.decode()
    assert "Clarity" in page and "VVS1" in page
    added = client.post("/diamonds/grades/", {"field": "cut", "action": "add", "code": "Ideal"})
    assert added.status_code == 302
    with tenant_context(tenant_a.id):
        assert StoneGrade.objects.filter(field="cut", code="Ideal").exists()
        assert Decimal(big.stone_weight_ct) == Decimal("0.740")
