"""Certificates: the IGI check (legacy GHD52) and certificate cards (GHD15, GHD19, GHD39). The
lab is never called: its answers are stubbed."""

import pytest
from django.core.cache import cache

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds import certificates, igi
from apps.diamonds.models import CertificateCheck, CheckStatus
from apps.diamonds.tests.test_diamonds import receive_ring, shop, switch_on  # noqa: F401
from apps.sales.services import post_sale
from conftest import login

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.4 a lab report"
IGI_STONES = ({"kind": "diamond", "shape": "round", "count": 1, "carat": "0.50", "color": "E",
               "clarity": "VS1", "lab": "IGI", "certificate_no": "LG612345678901"},)


@pytest.fixture
def lab(monkeypatch, settings, tmp_path):
    """IGI files LG61234567 (the printed number cut to ten characters)."""
    settings.MEDIA_ROOT = str(tmp_path)
    asked = []

    def get(url, headers=None):
        asked.append(url)
        if url.endswith("/LG61234567.pdf"):
            return 200, PDF, {}
        if "ReportDetail" in url:
            return 403, b"", {}  # the data service sits behind a bot wall
        return 404, b"", {}

    monkeypatch.setattr(igi, "_get", get)
    return asked


def test_the_lab_answers(lab):
    report = igi.fetch("LG 6123456789 01")
    assert report.status == "verified" and report.pdf == PDF and report.data == {}
    assert igi.fetch("NOPE123").status == "not_found"
    assert igi.fetch("  ").status == "error"


def test_a_check_is_kept_with_its_report(shop, lab):  # noqa: F811
    receive_ring(shop, stones=IGI_STONES)
    kept = certificates.check("LG612345678901")
    assert kept.status == CheckStatus.VERIFIED
    with kept.pdf.open("rb") as report:
        assert report.read() == PDF
    asked = len(lab)
    again = certificates.check("LG612345678901")  # kept: the lab is not asked again
    assert again.pk == kept.pk and len(lab) == asked
    certificates.check("LG612345678901", refresh=True)
    assert len(lab) > asked and CertificateCheck.objects.count() == 1
    missing = certificates.check("XX1")
    assert missing.status == CheckStatus.NOT_FOUND and not missing.pdf
    with pytest.raises(ValidationError):
        certificates.check("123", lab="GIA")
    with pytest.raises(ValidationError):
        certificates.check(" - ")


def test_pages_and_cards(tenant_a, lab):
    cache.clear()
    switch_on(tenant_a)
    client = login(tenant_a, language="en")
    with tenant_context(tenant_a.id):
        from apps.catalog.models import ProductFamily, Tracking
        from apps.catalog.services import CreateItemCategoryCommand, create_item_category
        from apps.parties.services import PartyData, create_customer, create_supplier
        from apps.sales.tests.test_sales import Shop

        shop_ = Shop()
        shop_.mountings = create_item_category(CreateItemCategoryCommand(
            code="3010", name="Diamond rings", product_family=ProductFamily.DIAMOND,
            tracking=Tracking.SERIALIZED, barcode_prefix=3010))
        shop_.dealer = create_supplier(PartyData(name="Antwerp Diamonds"))
        ring = receive_ring(shop_, stones=IGI_STONES)
        mona = create_customer(PartyData(name="Mona"))
        sale = post_sale(shop_.sale(shop_.line(ring), payment_terms="credit",
                                    customer_id=mona.pk))
    page = client.get("/diamonds/certificates/?q=LG612345678901").content.decode()
    assert ring.barcode in page and "Check with IGI" in page
    answer = client.post("/api/v1/diamonds/certificates/check/",
                         {"report_no": "LG612345678901", "refresh": True},
                         content_type="application/json").json()
    assert answer["status"] == "verified" and answer["pdf_url"]
    report = client.get(answer["pdf_url"])
    assert report.status_code == 200 and report["Content-Type"] == "application/pdf"
    assert b"".join(report.streaming_content) == PDF
    assert "Found at the lab" in client.get("/diamonds/certificates/?q=LG6123").content.decode()

    card = client.get(f"/diamonds/cards/?items={ring.pk}").content.decode()
    assert ring.barcode in card and "LG612345678901" in card and "Certificate" in card
    for_sale = client.get(f"/diamonds/cards/?sale={sale.pk}").content.decode()
    assert "Mona" in for_sale
    assert client.get("/diamonds/cards/?items=0").status_code == 404
    assert client.get(f"/diamonds/cards/?items={ring.pk}&format=pdf").status_code in (200, 503)
    assert "Certificate cards" in client.get(f"/sales/{sale.pk}/").content.decode()
    assert "Certificate card" in client.get(f"/stock/items/{ring.pk}/").content.decode()
