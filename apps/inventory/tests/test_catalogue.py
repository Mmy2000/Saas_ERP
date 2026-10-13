"""Photos of pieces and categories, and the photo catalogue (legacy GHMRK2 / GHMRK4)."""


import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.inventory import catalogue
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from conftest import login

pytestmark = pytest.mark.django_db
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()


def png(name="ring.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


def test_photos_and_prices(shop, media):
    ring_a, ring_b = shop.ring_a, shop.ring_b
    catalogue.set_photo(ring_a, png())
    assert ring_a.photo.name.startswith(f"tenants/{ring_a.tenant_id}/photos/")
    old = ring_a.photo.name
    catalogue.set_photo(ring_a, png("again.png"))
    assert not (media / old).exists()  # the replaced file is gone
    with pytest.raises(ValidationError):
        catalogue.set_photo(ring_a, SimpleUploadedFile("x.png", b"not an image"))

    items, _filters, prices = catalogue.pieces({"photos": "with"})
    assert [i.pk for i in items] == [ring_a.pk]
    items, _filters, prices = catalogue.pieces({"photos": "without"})
    assert ring_b.pk in [i.pk for i in items] and ring_a.pk not in [i.pk for i in items]

    catalogue.set_photo(ring_b.category, png("cat.png"))  # the category's photo stands in
    ring_b.refresh_from_db()
    assert catalogue.photo_url(ring_b) == ring_b.category.photo.url

    [entry] = catalogue.priced(*catalogue.pieces({"category": str(ring_a.category_id),
                                                  "w_max": str(ring_a.gross_weight_g)})[::2])
    assert entry.item == ring_a and entry.price == RING_A_TOTAL  # what the sales screen asks
    expensive = catalogue.priced(*catalogue.pieces({"p_min": str(RING_A_TOTAL + 1)})[::2])
    assert ring_a.pk not in [e.item.pk for e in expensive]

    catalogue.set_photo(ring_a, None)
    assert not ring_a.photo


def test_pages(tenant_a, shop, monkeypatch):
    client = login(tenant_a, language="en")
    answer = client.post(f"/stock/items/{shop.ring_a.pk}/photo/", {"photo": png()})
    assert answer.status_code == 302
    with tenant_context(tenant_a.id):
        shop.ring_a.refresh_from_db()
        url = shop.ring_a.photo.url
    assert url in client.get(f"/stock/items/{shop.ring_a.pk}/").content.decode()
    assert client.get(url).status_code == 200  # served to the shop's members
    page = client.get("/stock/catalogue/").content.decode()
    assert url in page and shop.ring_a.barcode in page and "A5 catalogue" in page
    assert shop.ring_a.barcode not in client.get(
        "/stock/catalogue/?photos=without").content.decode()
    answer = client.post(f"/stock/categories/{shop.ring_a.category_id}/photo/", {"photo": png()})
    assert answer.status_code == 302
    assert "Category photo" in client.get(
        f"/catalog/categories/{shop.ring_a.category_id}/").content.decode()

    import apps.printing.pdf as pdf

    seen = {}
    monkeypatch.setattr(pdf, "html_to_pdf", lambda html, paper: seen.setdefault("html", html)
                        and b"%PDF-1.7")
    made = client.get("/stock/catalogue/?format=pdf")
    assert made.status_code == 200 and made["Content-Type"] == "application/pdf"
    assert "size: A5" in seen["html"] and shop.ring_a.barcode in seen["html"]
