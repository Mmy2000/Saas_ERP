"""Natural or lab-grown stones, setting types, model classes and stone codes with a reference
price per carat (legacy Fd1 type, GHD13a, GHD9, GHD11)."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds.grades import add_grade, choices, delete_grade, used
from apps.diamonds.models import GradeField, ItemStone, StoneGrade
from apps.diamonds.services import reference_value, update_item
from apps.diamonds.stone_codes import StoneCodeInput, save_stone_code
from apps.diamonds.tests.test_diamonds import (
    receive_ring,
    shop,  # noqa: F401  (fixture)
)
from apps.printing.documents import stones_text
from apps.printing.labels import field_text
from conftest import login

pytestmark = pytest.mark.django_db


def _code(code_id=None, **kw):
    values = {"code": "RD-LG-1", "name": "Round lab-grown", "kind": "diamond",
              "origin": "lab_grown", "shape": "round", "color": "f", "clarity": "vs1",
              "price_per_ct": "400", "currency_code": "USD"}
    return save_stone_code(StoneCodeInput(**{**values, **kw}), code_id=code_id)


def test_stone_code_fills_the_stone_and_its_price(shop):
    code = _code()
    assert (code.color, code.clarity) == ("F", "VS1")  # the listed grades
    with pytest.raises(ValidationError):
        _code(code="rd-lg-1")  # the same code in another case
    with pytest.raises(ValidationError):
        _code(code="X", price_per_ct="-1")

    ring = receive_ring(shop, stones=({"code": "RD-LG-1", "carat": "0.50",
                                       "setting_type": "prong"},))
    stone = ItemStone.objects.get(item=ring)
    assert (stone.kind, stone.origin, stone.shape, stone.color, stone.clarity) == (
        "diamond", "lab_grown", "round", "F", "VS1")
    assert stone.setting_type == "Prong" and stone.code == code
    assert (stone.price_per_ct, stone.price_currency) == (Decimal("400.00"), "USD")
    assert reference_value([stone]) == {"USD": Decimal("200.00")}

    # A later price change does not move what the stone was entered at; saving the piece's
    # stones again keeps it.
    _code(code.pk, price_per_ct="500")
    update_item(ring.pk, stones=[{"code": "RD-LG-1", "carat": "0.50", "price_per_ct": "400.00",
                                  "price_currency": "USD"}])
    assert ItemStone.objects.get(item=ring).price_per_ct == Decimal("400.00")
    with pytest.raises(ValidationError):
        update_item(ring.pk, stones=[{"code": "NOPE", "carat": "0.5"}])
    with pytest.raises(ValidationError):
        update_item(ring.pk, stones=[{"carat": "0.5", "origin": "grown"}])

    ring.refresh_from_db()
    assert "Lab-grown" in stones_text(ring)
    assert field_text("origin", ring) == "Lab-grown"


def test_setting_types_and_model_classes(shop):
    lists = choices()
    assert "Prong" in lists["setting"] and lists["model"] == []
    add_grade(GradeField.MODEL, "Solitaire")
    add_grade(GradeField.MODEL, "Rings")
    ring = receive_ring(shop)
    update_item(ring.pk, stones=[{"carat": "0.5"}], model_classes=["solitaire", "Rings"])
    ring.refresh_from_db()
    assert ring.model_classes == ["Solitaire", "Rings"]
    with pytest.raises(ValidationError):
        update_item(ring.pk, stones=[{"carat": "0.5"}], model_classes=["Bridal"])
    solitaire = StoneGrade.objects.get(field=GradeField.MODEL, code="Solitaire")
    assert used(solitaire)
    with pytest.raises(DomainError):  # filed pieces keep it: switch it off instead
        delete_grade(solitaire.pk)


def test_pages(tenant_a, shop):
    with tenant_context(tenant_a.id):
        _code()
        add_grade(GradeField.MODEL, "Solitaire")
        ring = receive_ring(shop, stones=({"code": "RD-LG-1", "carat": "0.50"},))
        update_item(ring.pk, stones=[{"code": "RD-LG-1", "carat": "0.50", "price_per_ct": "400",
                                      "price_currency": "USD"}], model_classes=["Solitaire"])
    client = login(tenant_a, language="en")
    page = client.get("/diamonds/stone-codes/").content.decode()
    assert "RD-LG-1" in page and "400.00 USD" in page and "New stone code" in page
    answer = client.post("/diamonds/stone-codes/", {
        "code": "PR-N-2", "name": "Princess natural", "kind": "diamond", "origin": "natural",
        "shape": "princess", "price_per_ct": "1500", "currency": "USD", "is_active": "on"})
    assert answer.status_code == 302
    page = client.get(f"/stock/items/{ring.pk}/").content.decode()
    assert "Reference value" in page and "200.00 USD" in page and "Setting type" in page
    stock = client.get("/diamonds/?origin=lab_grown&model=Solitaire").content.decode()
    assert ring.barcode in stock
    assert ring.barcode not in client.get("/diamonds/?origin=natural").content.decode()
    lists = client.get("/diamonds/grades/").content.decode()
    assert "Setting type" in lists and "Model class" in lists
    assert "RD-LG-1" in client.get("/diamonds/receive/").content.decode()
