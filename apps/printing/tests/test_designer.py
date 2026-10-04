"""The drag-and-drop document designer: cleaning, rendering, preview, save and printing."""

import json

import pytest

from apps.core.tenancy import tenant_context
from apps.printing.builder import MAX_BLOCKS, clean_blocks, clean_page, preset
from apps.printing.models import DocumentDesign
from apps.printing.tests.test_documents import console, owner, sale  # noqa: F401  (fixtures)

pytestmark = pytest.mark.django_db
URL = "/settings/documents/sales_invoice/designer/"


def test_cleaning_keeps_only_known_safe_values():
    raw = [
        {"type": "items", "props": {"head_bg": "red; background:url(x)", "cell_pad": 999,
                                    "lines": "grid", "evil": "<script>"},
         "style": {"radius": 12, "mt": -5, "bg": "#123456", "color": "expression(1)",
                   "onclick": "x"}},
        {"type": "script", "props": {}},
        {"type": "columns", "props": {"count": 3},
         "children": [[{"type": "columns"}, {"type": "text", "props": {"text": "hi"}}], [], []]},
        "not a block",
    ]
    blocks = clean_blocks(raw)
    assert [b["type"] for b in blocks] == ["items", "columns"]
    items = blocks[0]
    assert items["props"]["head_bg"] == "#18181b"  # not a colour: back to the default
    assert items["props"]["cell_pad"] == 20 and items["props"]["lines"] == "grid"
    assert "evil" not in items["props"] and "onclick" not in items["style"]
    assert (items["style"]["radius"], items["style"]["mt"], items["style"]["bg"]) == (12, 0,
                                                                                   "#123456")
    assert items["style"]["color"] == ""
    columns = blocks[1]
    assert len(columns["children"]) == 3
    assert [b["type"] for b in columns["children"][0]] == ["text"]  # no columns in columns
    assert len(clean_blocks([{"type": "spacer"}] * (MAX_BLOCKS + 20))) == MAX_BLOCKS
    assert clean_page({"mt": 500, "size": "x", "bg": "#fff"}) == {
        **clean_page({}), "mt": 40}


def test_presets_are_valid():
    for name in ("classic", "modern", "thermal"):
        blocks = preset(name)
        assert blocks and clean_blocks(blocks) == blocks


def test_designer_preview_and_save(owner, tenant_a):  # noqa: F811
    page = owner.get(URL)
    assert page.status_code == 200 and "designer-data" in page.content.decode()
    state = {"blocks": [
        {"type": "title", "props": {"text": "My invoice"}, "style": {"radius": 14, "bg": "#fef3c7",
                                                                    "pt": 4, "pb": 4}},
        {"type": "items", "props": {"stripes": True, "head_bg": "#0f766e"}},
        {"type": "text", "props": {"source": "terms"}},  # empty terms: not printed
    ], "page": {"mt": 20, "ml": 5}, "paper": "a5", "lang": "en"}
    preview = owner.post(URL + "preview/", json.dumps(state), content_type="application/json")
    html = preview.content.decode()
    assert preview.status_code == 200 and "My invoice" in html
    assert "border-radius:14px" in html and "#0f766e" in html and "data-block=" in html
    assert "20mm 12mm 12mm 5mm" in html and "size: A5" in html
    with tenant_context(tenant_a.id):
        assert not DocumentDesign.objects.exists()

    saved = owner.post(URL + "save/", json.dumps(state), content_type="application/json")
    assert saved.status_code == 200 and saved.json()["ok"]
    with tenant_context(tenant_a.id):
        design = DocumentDesign.objects.get()
    assert (design.layout, design.paper, design.lang) == ("builder", "a5", "en")
    assert [b["type"] for b in design.blocks] == ["title", "items", "text"]

    assert owner.post(URL + "save/", "not json", content_type="application/json"
                      ).status_code == 400
    assert owner.post(URL + "save/", "x" * 300_000, content_type="application/json"
                      ).status_code == 400


def test_a_designed_invoice_prints(owner, tenant_a, sale):  # noqa: F811
    with tenant_context(tenant_a.id):
        DocumentDesign.objects.create(doc_type="sales_invoice", layout="builder",
                                      blocks=preset("modern"), page={"mt": 8})
    html = owner.get(f"/sales/{sale.pk}/print/").content.decode()
    assert sale.number in html and 'class="sheet builder"' in html
    assert "data-block=" not in html  # no designer markers on real prints
    assert "parent.postMessage" not in html


def test_designer_permissions_and_console(owner, console, tenant_a, make_member):  # noqa: F811
    make_member(tenant_a, "viewer", "viewer")
    from conftest import login

    viewer = login(tenant_a, username="viewer")
    assert viewer.get(URL).status_code == 403
    assert viewer.post(URL + "save/", "{}", content_type="application/json").status_code == 403

    base = f"/clients/{tenant_a.pk}/documents/sales_invoice/designer/"
    assert console.get(base).status_code == 200
    state = {"blocks": preset("thermal"), "page": {}, "paper": "roll80", "lang": "ar"}
    assert console.post(base + "preview/", json.dumps(state),
                        content_type="application/json").status_code == 200
    assert console.post(base + "save/", json.dumps(state),
                        content_type="application/json").json()["ok"]
    with tenant_context(tenant_a.id):
        assert DocumentDesign.objects.get().paper == "roll80"
