import pytest

from apps.core.tenancy import tenant_context
from apps.sales.models import SalesInvoice
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        return Shop()


def _sale(shop, **extra):
    return {"branch": shop.branch.pk, "lines": [{"barcode": shop.ring_a.barcode}],
            "payments": [{"kind": "cash", "currency": "EGP", "amount": str(RING_A_TOTAL)}],
            **extra}


def _post(client, url, body, key):
    return client.post(url, body, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)


def test_quote_then_sell(tenant_a, shop):
    client = login(tenant_a)
    quote = client.post("/api/v1/sales/quote/", _sale(shop, payments=[]),
                        content_type="application/json").json()
    assert quote["totals"]["total"] == "18392.85"
    assert quote["totals"]["remaining"] == "18392.85"
    assert [p["code"] for p in quote["problems"]] == ["SALES_NOT_FULLY_PAID"]

    created = _post(client, "/api/v1/sales/invoices/", _sale(shop), "sale-1")
    assert created.status_code == 201, created.content
    assert created.json()["number"].endswith("-SI-2026-000001")


def test_same_key_never_posts_twice(tenant_a, shop):
    client = login(tenant_a)
    first = _post(client, "/api/v1/sales/invoices/", _sale(shop), "double-click")
    second = _post(client, "/api/v1/sales/invoices/", _sale(shop), "double-click")
    assert first.status_code == second.status_code == 201
    assert second["Idempotent-Replay"] == "true"
    assert first.json()["id"] == second.json()["id"]
    with tenant_context(tenant_a.id):
        assert SalesInvoice.objects.count() == 1


def test_key_rules(tenant_a, shop):
    client = login(tenant_a)
    missing = client.post("/api/v1/sales/invoices/", _sale(shop), content_type="application/json")
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    # A failed attempt releases its key, so a corrected retry with the same key works.
    failed = _post(client, "/api/v1/sales/invoices/", _sale(shop, payments=[]), "k1")
    assert failed.json()["error"]["code"] == "SALES_NOT_FULLY_PAID"
    assert _post(client, "/api/v1/sales/invoices/", _sale(shop), "k1").status_code == 201
    reused = _post(client, "/api/v1/sales/invoices/1/void/", {}, "k1")
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_void_via_api_and_pages(tenant_a, shop):
    client = login(tenant_a)
    invoice = _post(client, "/api/v1/sales/invoices/", _sale(shop), "s").json()
    for path in ("/sales/", "/sales/new/", f"/sales/{invoice['id']}/", "/"):
        assert client.get(path).status_code == 200, path
    voided = _post(client, f"/api/v1/sales/invoices/{invoice['id']}/void/", {"reason": "x"}, "v")
    assert voided.json()["status"] == "voided"


def test_seller_without_sell_permission(tenant_a, shop, make_member):
    make_member(tenant_a, "val", "viewer")
    client = login(tenant_a, "val")
    assert client.post("/api/v1/sales/quote/", _sale(shop),
                       content_type="application/json").status_code == 403
    assert client.get("/sales/new/").status_code == 403
