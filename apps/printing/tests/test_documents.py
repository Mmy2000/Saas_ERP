"""Printed documents: built-in layouts, the client's options, custom HTML (console only)."""

import pytest

from apps.core.tenancy import tenant_context
from apps.printing.models import DocumentDesign
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def sale(tenant_a):
    """A posted retail sale (the sales test fixture)."""
    from apps.sales.services import post_sale
    from apps.sales.tests.test_sales import RING_A_TOTAL, Shop

    with tenant_context(tenant_a.id):
        shop = Shop()
        return post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))


@pytest.fixture
def owner(tenant_a):
    client = login(tenant_a)
    client.cookies["django_language"] = "en"
    return client


def _design(tenant, **values):
    with tenant_context(tenant.id):
        design, _ = DocumentDesign.objects.update_or_create(doc_type="sales_invoice",
                                                           defaults=values)
        return design


def test_print_a_sale_in_each_layout(owner, tenant_a, sale):
    page = owner.get(f"/sales/{sale.pk}/print/")
    html = page.content.decode()
    assert page.status_code == 200 and sale.number in html
    assert "Sales invoice" in html and "@page { size: A4" in html and "cl-table" in html
    assert owner.get(f"/sales/{sale.pk}/").content.decode().count(f"/sales/{sale.pk}/print/")

    _design(tenant_a, layout="modern", paper="a5", lang="ar", copies=2)
    html = owner.get(f"/sales/{sale.pk}/print/").content.decode()
    assert "md-band" in html and "size: A5" in html and "فاتورة" in html
    assert html.count('<div class="sheet">') == 2

    _design(tenant_a, layout="classic", paper="roll80", lang="both", copies=1)
    html = owner.get(f"/sales/{sale.pk}/print/").content.decode()
    assert 'class="th"' in html and "80mm auto" in html  # a roll always gets the slip
    assert "Sales invoice" in html and "فاتورة" in html


def test_owner_sets_the_design_with_a_live_preview(owner, tenant_a):
    assert owner.get("/settings/documents/").status_code == 200
    page = owner.get("/settings/documents/sales_invoice/")
    assert page.status_code == 200 and "data-design-form" in page.content.decode()
    data = {"layout": "modern", "paper": "a4", "lang": "en", "color": "#123456",
            "show_logo": "on", "copies": "1", "header_note": "Gold of trust",
            "footer_note": "See you again", "terms": "No returns after 14 days.",
            "col_karat": "on", "col_weight": "on", "opt_show_payments": "on"}
    preview = owner.post("/settings/documents/sales_invoice/preview/", data)
    html = preview.content.decode()
    assert preview.status_code == 200 and "Gold of trust" in html and "#123456" in html
    assert "S-2026-000123" in html  # sample data
    with tenant_context(tenant_a.id):
        assert not DocumentDesign.objects.exists()  # a preview saves nothing

    assert owner.post("/settings/documents/sales_invoice/", data).status_code == 302
    with tenant_context(tenant_a.id):
        design = DocumentDesign.objects.get()
    assert (design.layout, design.color, design.footer_note) == ("modern", "#123456",
                                                                 "See you again")
    assert design.enabled_columns("sales_invoice") == ["piece", "karat", "weight", "total"]
    assert design.option("sales_invoice", "show_payments") is True
    assert design.option("sales_invoice", "show_seller") is False


def test_documents_need_the_settings_permission(tenant_a, make_member):
    make_member(tenant_a, "viewer", "viewer")
    viewer = login(tenant_a, username="viewer")
    assert viewer.get("/settings/documents/").status_code == 403
    assert viewer.post("/settings/documents/sales_invoice/preview/", {}).status_code == 403
    assert login(tenant_a).get("/settings/documents/nope/").status_code == 404


@pytest.fixture
def console(db):
    from django.test import Client

    from apps.iam.models import User
    from conftest import PASSWORD

    User.objects.create_user(email="ops@gweb.test", password=PASSWORD, display_name="Ops",
                             is_platform_staff=True)
    client = Client(HTTP_HOST="admin.localhost")
    client.cookies["django_language"] = "en"
    assert client.post("/login/", {"username": "ops@gweb.test", "password": PASSWORD}
                       ).status_code == 302
    return client


BASE = {"layout": "classic", "paper": "a4", "lang": "en", "copies": "1", "show_logo": "on"}


def test_staff_write_a_custom_design(console, owner, tenant_a, sale):
    url = f"/clients/{tenant_a.pk}/documents/sales_invoice/"
    page = console.get(url)
    assert page.status_code == 200 and "Start from the classic layout" in page.content.decode()
    assert "documents/sales_invoice/" in console.get(f"/clients/{tenant_a.pk}/").content.decode()

    html = '<h1 class="mine">{{ doc.company.name }} · {{ doc.number }}</h1>' \
           '{% for row in doc.rows %}<p>{{ row.piece }}</p>{% endfor %}'
    preview = console.post(url + "preview/", {**BASE, "custom_html": html, "use_custom": "on"})
    assert 'class="mine"' in preview.content.decode()
    assert "S-2026-000123" in preview.content.decode()

    assert console.post(url, {**BASE, "custom_html": html, "use_custom": "on"}
                        ).status_code == 302
    printed = owner.get(f"/sales/{sale.pk}/print/").content.decode()
    assert 'class="mine"' in printed and sale.number in printed and "cl-table" not in printed

    # Every change keeps the previous HTML, and it can come back.
    console.post(url, {**BASE, "custom_html": "<h1>v2</h1>", "use_custom": "on"})
    with tenant_context(tenant_a.id):
        design = DocumentDesign.objects.get()
        version = design.versions.get()
    assert version.custom_html == html
    console.post(f"{url}versions/{version.pk}/restore/")
    with tenant_context(tenant_a.id):
        design.refresh_from_db()
    assert design.custom_html == html

    # Off: the layout prints again.
    console.post(url, {**BASE, "custom_html": html})
    assert "cl-table" in owner.get(f"/sales/{sale.pk}/print/").content.decode()


def test_custom_designs_are_sandboxed(console, owner, tenant_a, sale):
    url = f"/clients/{tenant_a.pk}/documents/sales_invoice/"
    # No server templates or files...
    leak = console.post(url + "preview/", {**BASE, "use_custom": "on",
                                           "custom_html": '{% include "base.html" %}'})
    body = leak.content.decode()
    assert "standard layout is shown" in body and "cl-table" in body
    # ...a broken template falls back to the layout instead of failing the shop...
    broken = console.post(url + "preview/", {**BASE, "use_custom": "on",
                                             "custom_html": "{% for x in %}"})
    assert broken.status_code == 200 and "cl-table" in broken.content.decode()
    # ...and values are escaped.
    with tenant_context(tenant_a.id):
        sale.customer_phone = "<script>alert(1)</script>"
        sale.save(update_fields=["customer_phone"])
    console.post(url, {**BASE, "use_custom": "on",
                       "custom_html": "{% for l, v in doc.party %}<i>{{ v }}</i>{% endfor %}"})
    printed = owner.get(f"/sales/{sale.pk}/print/").content.decode()
    assert "<script>alert(1)" not in printed and "&lt;script&gt;" in printed


def test_clients_cannot_set_custom_html(owner, tenant_a):
    owner.post("/settings/documents/sales_invoice/", {
        **BASE, "custom_html": "<h1>mine</h1>", "use_custom": "on"})
    with tenant_context(tenant_a.id):
        design = DocumentDesign.objects.get()
    assert (design.custom_html, design.use_custom) == ("", False)


def test_every_document_type_renders_in_every_layout(owner):
    from apps.printing.builder import preset
    from apps.printing.documents import TYPES

    for doc_type in TYPES:
        page = owner.get(f"/settings/documents/{doc_type}/")
        assert page.status_code == 200, doc_type
        for layout, paper in (("classic", "a4"), ("modern", "a5"), ("thermal", "roll80")):
            html = owner.post(f"/settings/documents/{doc_type}/preview/", {
                **BASE, "layout": layout, "paper": paper, "lang": "both"}).content.decode()
            assert 'class="sheet' in html and "2026" in html, (doc_type, layout)
        for name in ("classic", "modern", "thermal"):
            import json

            state = {"blocks": preset(name), "page": {}, "paper": "a4", "lang": "ar"}
            html = owner.post(f"/settings/documents/{doc_type}/designer/preview/",
                              json.dumps(state), content_type="application/json")
            assert html.status_code == 200 and "data-block=" in html.content.decode(), \
                (doc_type, name)


def test_documents_follow_the_sales_invoice_until_given_their_own(owner, tenant_a):
    from apps.printing.render import design_for

    page = owner.get("/settings/documents/").content.decode()
    assert "Reservation" in page and "Same as Sales invoice" in page

    _design(tenant_a, layout="modern", paper="a5", footer_note="Thanks!")
    with tenant_context(tenant_a.id):
        followed = design_for("reservation")
    assert (followed.layout, followed.paper, followed.footer_note) == ("modern", "a5", "Thanks!")

    # Its own design: saved and used; its columns are always its own.
    response = owner.post("/settings/documents/reservation/", {
        **BASE, "follows": "", "layout": "classic", "col_karat": "on"})
    assert response.status_code == 302
    with tenant_context(tenant_a.id):
        own = design_for("reservation")
        assert own.layout == "classic"
        assert DocumentDesign.objects.get(doc_type="reservation").follows == ""
    # Following again keeps its columns but takes the other one's look.
    owner.post("/settings/documents/reservation/", {**BASE, "follows": "sales_invoice"})
    with tenant_context(tenant_a.id):
        assert design_for("reservation").layout == "modern"

    # No loops: the sales invoice cannot follow a document that follows it.
    looped = owner.post("/settings/documents/sales_invoice/", {**BASE, "follows": "reservation"})
    assert looped.status_code == 200 and "already uses this one" in looped.content.decode()
