"""Server-side PDFs (headless Chromium): documents, reports and statements, and what the
renderer may load."""

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from apps.core.tenancy import tenant_context
from apps.printing import pdf
from apps.printing.tests.test_documents import _design, owner, sale  # noqa: F401  (fixtures)
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module")
def chromium():
    try:
        pdf.html_to_pdf("<p>ok</p>")
    except pdf.PdfUnavailable:
        pytest.skip("Chromium is not installed (python -m playwright install chromium)")


def _boxes(content: bytes) -> list[tuple[float, float]]:
    """(width, height) in points of each page."""
    return [(float(w), float(h)) for w, h in
            re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", content)]


def _is_pdf(response):
    assert response.status_code == 200, response.content[:300]
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("attachment")
    assert response.content.startswith(b"%PDF-")
    return response.content


def test_a_document_as_pdf_in_its_design(chromium, owner, tenant_a, sale):  # noqa: F811
    response = owner.get(f"/print/sales_invoice/{sale.pk}/pdf/")
    content = _is_pdf(response)
    assert sale.number in response["Content-Disposition"]
    (width, height), *_ = _boxes(content)
    assert round(width) == 595 and round(height) == 842  # A4

    _design(tenant_a, layout="classic", paper="roll80", lang="ar", copies=1)
    content = _is_pdf(owner.get(f"/print/sales_invoice/{sale.pk}/pdf/"))
    (width, height), *_ = _boxes(content)
    assert round(width) == 227 and height > width  # 80 mm wide, as long as the slip

    inline = owner.get(f"/print/sales_invoice/{sale.pk}/pdf/?inline=1")
    assert inline["Content-Disposition"].startswith("inline")
    page = owner.get(f"/sales/{sale.pk}/").content.decode()
    assert f"/print/sales_invoice/{sale.pk}/pdf/" in page  # the PDF button


def test_pdf_has_the_print_permission_and_scope(chromium, tenant_a, tenant_b, sale):  # noqa: F811
    other = login(tenant_b)
    assert other.get(f"/print/sales_invoice/{sale.pk}/pdf/").status_code == 404
    assert login(tenant_a).get("/print/nope/1/pdf/").status_code == 404
    anonymous = login(tenant_a)
    anonymous.logout()
    assert anonymous.get(f"/print/sales_invoice/{sale.pk}/pdf/").status_code == 302


def test_reports_and_statements_as_pdf(chromium, owner, tenant_a, sale):  # noqa: F811
    content = _is_pdf(owner.get("/reports/sales/?format=pdf&group=day"))
    (width, height), *_ = _boxes(content)
    assert width > height  # a wide report goes sideways
    content = _is_pdf(owner.get("/accounting/profit-and-loss/?format=pdf"))
    (width, height), *_ = _boxes(content)
    assert width < height
    assert "format=pdf" in owner.get("/reports/sales/").content.decode()

    with tenant_context(tenant_a.id):
        from apps.parties.selectors import parties_with_role
        from apps.treasury.models import CashBox

        supplier = parties_with_role("supplier").first()  # who the shop bought the rings from
        box = CashBox.objects.first()
    response = owner.get(f"/suppliers/{supplier.pk}/statement/?format=pdf")
    _is_pdf(response)
    assert "Statement" in response["Content-Disposition"]
    assert "format=pdf" in owner.get(f"/suppliers/{supplier.pk}/statement/").content.decode()
    _is_pdf(owner.get(f"/treasury/box/{box.pk}/statement/?from=2026-01-01&format=pdf"))


def test_without_chromium_the_shop_is_told(owner, sale, monkeypatch):  # noqa: F811
    def broken(*args, **kwargs):
        raise pdf.PdfUnavailable("no browser")

    monkeypatch.setattr(pdf, "html_to_pdf", broken)
    response = owner.get(f"/print/sales_invoice/{sale.pk}/pdf/")
    assert response.status_code == 503 and b"Save as PDF" in response.content
    assert owner.get("/reports/sales/?format=pdf").status_code == 503


def test_local_files_only():
    content, kind = pdf._local_file("/static/core/fonts/plex-arabic-400.woff2")
    assert content and kind == "font/woff2"
    assert pdf._local_file("/static/core/fonts/nope.woff2") is None
    assert pdf._local_file("/media/../config/settings/base.py") is None
    assert pdf._local_file("/etc/passwd") is None


def test_the_renderer_fetches_nothing_and_runs_no_scripts(chromium):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        html = (f'<img src="{base}/img.png"><link rel="stylesheet" href="{base}/x.css">'
                f'<script>fetch("{base}/script"); document.write("ran")</script>'
                f'<iframe src="{base}/frame"></iframe>')
        assert pdf.html_to_pdf(html).startswith(b"%PDF-")
    finally:
        server.shutdown()
    assert hits == []
