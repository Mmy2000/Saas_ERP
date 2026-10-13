"""Fonts uploaded for labels and printed documents (legacy GHSH14 font manager)."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.printing import fonts
from apps.printing.models import DocumentDesign, LabelTemplate, PrintFont
from apps.printing.services import template_from
from apps.printing.tests.test_printing import shop  # noqa: F401  (fixture)
from conftest import login

pytestmark = pytest.mark.django_db
TTF = b"\x00\x01\x00\x00" + b"\x00" * 60
WOFF2 = b"wOF2" + b"\x00" * 60


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def upload(name="Amiri.ttf", content=TTF):
    return SimpleUploadedFile(name, content, content_type="font/ttf")


def test_fonts_are_checked_and_used(shop, media):
    font = fonts.add_font("", upload())
    assert (font.name, font.format) == ("Amiri", "truetype")
    assert fonts.add_font("Web", upload("web.woff2", WOFF2)).format == "woff2"
    with pytest.raises(ValidationError):
        fonts.add_font("Fake", upload("fake.ttf", b"<svg>not a font</svg>"))
    with pytest.raises(ValidationError):
        fonts.add_font("amiri", upload())  # the same name
    face = str(fonts.font_face(font))
    assert f'font-family: "PrintFont{font.pk}"' in face and 'format("truetype")' in face
    assert str(fonts.family(None)).startswith('"Inter"')

    tag = LabelTemplate.objects.first()
    template_from({"name": tag.name, "font": str(font.pk), "fields": tag.fields,
                   "fields_b": tag.fields_b, "layout": tag.layout}, tag).save()
    with pytest.raises(ValidationError):
        template_from({"name": "x", "font": "999999", "fields": ["barcode"]})
    file_name = font.file.name
    fonts.delete_font(font.pk)
    tag.refresh_from_db()
    assert tag.font is None and not (media / file_name).exists()


def test_label_and_document_pages(tenant_a, shop):
    with tenant_context(tenant_a.id):
        font = fonts.add_font("Amiri", upload())
        LabelTemplate.objects.update(font=font)
        DocumentDesign.objects.update_or_create(doc_type="sales_invoice",
                                                defaults={"font": font})
        sale_id = __import__("apps.sales.models", fromlist=["SalesInvoice"]).SalesInvoice \
            .objects.values_list("pk", flat=True).first()
    client = login(tenant_a, language="en")
    page = client.get("/settings/labels/").content.decode()
    assert "Amiri" in page and "Add font" in page
    labels = client.get(f"/stock/labels/?item={shop.ring_a.pk}").content.decode()
    assert f'"PrintFont{font.pk}"' in labels and font.file.url in labels
    assert client.get(font.file.url).status_code == 200
    assert f"/settings/labels/{LabelTemplate.objects.first().pk}/" in page
    form = client.get(f"/settings/labels/{LabelTemplate.objects.first().pk}/").content.decode()
    assert "The system&#x27;s font" in form or "The system's font" in form
    if sale_id:
        printed = client.get(f"/print/sales_invoice/{sale_id}/").content.decode()
        assert f'"PrintFont{font.pk}"' in printed
    design = client.get("/settings/documents/sales_invoice/").content.decode()
    assert "Amiri" in design
    answer = client.post("/settings/labels/fonts/", {"name": "Second", "file": upload("b.ttf")})
    assert answer.status_code == 302
    with tenant_context(tenant_a.id):
        assert PrintFont.objects.count() == 2
