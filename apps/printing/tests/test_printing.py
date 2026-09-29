from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.printing.barcode import PATTERNS, code128, symbols
from apps.printing.labels import build_label, ensure_default_templates, paginate, zpl
from apps.printing.models import LabelTemplate, Layout, Media
from apps.printing.services import create_template, delete_template, template_from
from apps.purchasing.models import SupplierInvoice
from apps.sales.tests.test_sales import Shop
from conftest import login

pytestmark = pytest.mark.django_db


class TestCode128:
    def test_table(self):
        assert len(PATTERNS) == 107 and len(set(PATTERNS)) == 107
        assert all(sum(map(int, p)) == (13 if i == 106 else 11) for i, p in enumerate(PATTERNS))

    def test_numeric_codes_use_set_c(self):
        # Start C, 10 10 00 00 03, checksum (105 + 10 + 20 + 0 + 0 + 15) mod 103 = 47, stop.
        assert symbols("1010000003") == [105, 10, 10, 0, 0, 3, 47, 106]
        assert symbols("123") == [105, 12, 100, 19, 65, 106]  # odd digit switches to set B
        assert symbols("AB-1")[0] == 104
        with pytest.raises(ValueError):
            symbols("ذهب")

    def test_svg(self):
        barcode = code128("1010000003")
        assert barcode.total_modules == 8 * 11 + 2 + 20  # symbols + stop's extra bar + quiet zone
        assert barcode.svg().count("<rect") == 1 + 3 * 7 + 4  # background + bars


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        ensure_default_templates("en")
        yield Shop()


def test_default_templates_and_labels(shop):
    tail, sheet = LabelTemplate.objects.order_by("-is_default", "name")
    assert tail.is_default and tail.layout == Layout.SPLIT and tail.area_width_mm == 25
    with translation.override("en"):
        label = build_label(tail, shop.ring_a, "Shop")
    assert [kind for kind, _v in label.parts[0].elements] == ["barcode", "text"]
    assert [v for _k, v in label.parts[1].elements] == ["18K", "5.000 g", "Making 250"]
    pages = paginate(sheet, [label] * 30, skip=2)
    assert [len(page.cells) for page in pages] == [24, 8] and pages[0].cells[:2] == [None, None]
    assert len(paginate(tail, [label] * 3)) == 3


def test_zpl(shop):
    tail = LabelTemplate.objects.get(is_default=True)
    out = zpl(tail, [shop.ring_a, shop.ring_b], copies=2)
    assert out.count("^XA") == 2 and "^PQ2^XZ" in out
    assert f"^FD{shop.ring_a.barcode}^FS" in out and "^PW560^LL96" in out
    assert "^FD18K^FS" in out and "^FD5.000 g^FS" in out
    assert "^BY2^BCN" in out  # 2-dot bars: 90 modules × 0.25 mm fit the 25 mm wing
    assert "^FO368," in out  # second wing: 25 mm + 20 mm fold + 1 mm margin = 368 dots


def test_template_rules(shop):
    with pytest.raises(ValidationError) as exc:
        template_from({"name": "", "width_mm": "5", "fields": []})
    assert {"name", "width_mm", "fields"} <= set(exc.value.fields)
    with pytest.raises(ValidationError) as exc:  # 4 × 60 mm do not fit across A4
        template_from({"name": "x", "media": Media.SHEET, "width_mm": "60", "height_mm": "20",
                       "columns": "4", "rows": "1", "fields": ["code"]})
    assert "columns" in exc.value.fields
    made = create_template({"name": "Small", "width_mm": "40", "height_mm": "20",
                            "fields": ["code", "barcode", "code"], "is_default": True})
    assert made.fields == ["code", "barcode"] and made.is_default
    assert LabelTemplate.objects.filter(is_default=True).count() == 1
    delete_template(made.pk)
    assert LabelTemplate.objects.filter(is_default=True).count() == 1


def test_pages_and_permissions(tenant_a, shop, make_member):
    client = login(tenant_a)
    invoice = SupplierInvoice.objects.get()
    page = client.get(f"/stock/labels/?invoice={invoice.pk}")
    assert page.status_code == 200
    assert page.content.decode().count('class="label"') == 2
    sheet = LabelTemplate.objects.get(media=Media.SHEET)
    page = client.get(f"/stock/labels/?item={shop.ring_a.pk}&template={sheet.pk}&copies=3&skip=1")
    assert page.content.decode().count('class="bars"') == 3
    download = client.get(f"/stock/labels/zpl/?item={shop.ring_a.pk}")
    assert download["Content-Disposition"].endswith('labels.zpl"') and b"^XA" in download.content
    for path in ("/stock/labels/", "/settings/labels/", "/settings/labels/new/",
                 f"/settings/labels/{sheet.pk}/", "/settings/labels/preview/?width_mm=50&"
                 "height_mm=20&fields=barcode&fields=code", "/stock/",
                 f"/stock/items/{shop.ring_a.pk}/", f"/purchasing/{invoice.pk}/"):
        assert client.get(path).status_code == 200, path
    created = client.post("/api/v1/printing/templates/", {
        "name": "Ring tag", "width_mm": "30", "height_mm": "15", "fields": ["barcode", "karat"]},
        content_type="application/json")
    assert created.status_code == 201, created.content
    assert created.json()["width_mm"] == "30.00"

    make_member(tenant_a, "viewer", role="viewer")
    other = login(tenant_a, "viewer")
    assert other.get(f"/stock/labels/?item={shop.ring_a.pk}").status_code == 403
    assert other.get("/settings/labels/").status_code == 403
    assert Decimal(created.json()["height_mm"]) == 15
