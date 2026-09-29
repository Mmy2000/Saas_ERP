import pytest

from apps.catalog.models import Karat, ProductFamily, Tracking
from apps.catalog.services import CreateItemCategoryCommand, create_item_category
from apps.core.tenancy import tenant_context
from apps.org.models import Branch
from apps.parties.services import PartyData, create_supplier
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def ids(tenant_a):
    with tenant_context(tenant_a.id):
        rings = create_item_category(CreateItemCategoryCommand(
            code="1010", name="Rings", product_family=ProductFamily.GOLD,
            tracking=Tracking.SERIALIZED, barcode_prefix=1010))
        chain = create_item_category(CreateItemCategoryCommand(
            code="2020", name="Chain", product_family=ProductFamily.GOLD, tracking=Tracking.BULK))
        return {"branch": Branch.objects.get(code=1).pk, "rings": rings.pk, "chain": chain.pk,
                "k18": Karat.objects.get(code=18).pk, "k21": Karat.objects.get(code=21).pk,
                "supplier": create_supplier(PartyData(name="Factory")).pk}


def _payload(ids, **extra):
    return {"supplier": ids["supplier"], "branch": ids["branch"], "business_date": "2026-09-24",
            "currency": "EGP", "lines": [
                {"category": ids["rings"], "karat": ids["k18"], "piece_weights": ["5.25", "6.1"],
                 "making_cost_rate": "150", "list_making_rate": "250"},
                {"category": ids["chain"], "karat": ids["k21"], "gross_weight_g": "20",
                 "qty": 4, "making_cost_rate": "40"},
            ], **extra}


def test_draft_edit_post_void(tenant_a, ids):
    client = login(tenant_a)
    created = client.post("/api/v1/purchasing/invoices/", _payload(ids),
                          content_type="application/json")
    assert created.status_code == 201, created.content
    invoice = created.json()
    assert (invoice["status"], invoice["number"]) == ("draft", None)

    edited = client.put(f"/api/v1/purchasing/invoices/{invoice['id']}/",
                        _payload(ids, supplier_reference="INV-77"),
                        content_type="application/json")
    assert edited.json()["supplier_reference"] == "INV-77"

    posted = client.post(f"/api/v1/purchasing/invoices/{invoice['id']}/post/")
    assert posted.status_code == 200, posted.content
    body = posted.json()
    assert body["status"] == "posted" and body["number"].endswith("-PI-2026-000001")
    barcodes = [p["barcode"] for p in body["lines"][0]["pieces"]]
    assert barcodes == ["1010000001", "1010000002"]

    items = client.get("/api/v1/inventory/items/?status=in_stock").json()["results"]
    assert len(items) == 2 and "cost_amount" in items[0]
    assert client.get("/api/v1/inventory/lots/").json()["results"][0]["gross_weight_g"] == "20.000"

    # Posted documents are immutable.
    assert client.put(f"/api/v1/purchasing/invoices/{invoice['id']}/", _payload(ids),
                      content_type="application/json").status_code == 422

    voided = client.post(f"/api/v1/purchasing/invoices/{invoice['id']}/void/",
                         {"reason": "test"}, content_type="application/json")
    assert voided.json()["status"] == "voided"
    assert client.get("/api/v1/inventory/items/?status=in_stock").json()["results"] == []


def test_line_errors_point_at_the_line(tenant_a, ids):
    payload = _payload(ids)
    payload["lines"][0]["piece_weights"] = []
    error = login(tenant_a).post("/api/v1/purchasing/invoices/", payload,
                                 content_type="application/json").json()["error"]
    assert "piece_weights" in error["fields"]["lines"]["0"]


def test_permissions(tenant_a, ids, make_member):
    owner = login(tenant_a)
    draft = owner.post("/api/v1/purchasing/invoices/", _payload(ids),
                       content_type="application/json").json()
    owner.post(f"/api/v1/purchasing/invoices/{draft['id']}/post/")

    make_member(tenant_a, "val", "viewer")
    viewer = login(tenant_a, "val")
    assert viewer.get("/api/v1/purchasing/invoices/").status_code == 200
    assert viewer.post("/api/v1/purchasing/invoices/", _payload(ids),
                       content_type="application/json").status_code == 403
    item = viewer.get("/api/v1/inventory/items/").json()["results"][0]
    assert "cost_amount" not in item  # cost is omitted without inventory.item.view_cost


def test_pages_render(tenant_a, ids):
    client = login(tenant_a)
    invoice = client.post("/api/v1/purchasing/invoices/", _payload(ids),
                          content_type="application/json").json()
    for path in (f"/purchasing/{invoice['id']}/", f"/purchasing/{invoice['id']}/edit/"):
        assert client.get(path).status_code == 200, path
    client.post(f"/api/v1/purchasing/invoices/{invoice['id']}/post/")
    item = client.get("/api/v1/inventory/items/").json()["results"][0]
    for path in (f"/purchasing/{invoice['id']}/", f"/stock/items/{item['id']}/", "/stock/",
                 "/stock/?q=1010000001", "/"):
        response = client.get(path)
        assert response.status_code == 200, path
    assert "1010000001" in client.get("/stock/?q=1010000001").content.decode()
