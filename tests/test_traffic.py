"""Client traffic: counting, the requests-per-minute limit, and the console's Traffic pages."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.iam.models import User
from apps.platform.tenants import traffic
from apps.platform.tenants.models import (
    PlatformEvent,
    TenantRouteTraffic,
    TenantStatus,
    TenantTraffic,
)
from conftest import PASSWORD, login

pytestmark = pytest.mark.django_db
HOST = "admin.localhost"


@pytest.fixture
def console(db):
    staff = User.objects.create_user(email="ops@gweb.test", password=PASSWORD,
                                     display_name="Ops", is_platform_staff=True)
    client = Client(HTTP_HOST=HOST)
    client.cookies["django_language"] = "en"
    assert client.post("/login/", {"username": staff.email, "password": PASSWORD}
                       ).status_code == 302
    return client


def _minute(tenant):
    return TenantTraffic.objects.get(tenant=tenant, minute=traffic.current_minute())


def test_requests_are_counted_per_client_and_route(tenant_a, tenant_b):
    client = login(tenant_a)  # the login POST counts too
    assert client.get("/").status_code == 200
    row = _minute(tenant_a)
    assert (row.requests, row.throttled, row.errors) == (2, 0, 0)
    assert row.total_ms >= row.max_ms >= 0
    routes = set(TenantRouteTraffic.objects.filter(tenant=tenant_a)
                 .values_list("route", flat=True))
    assert {"POST /login/", "GET /"} <= routes
    assert not TenantTraffic.objects.filter(tenant=tenant_b).exists()


def test_platform_hosts_and_static_files_are_not_counted(console, tenant_a):
    console.get("/")
    Client(HTTP_HOST=f"{tenant_a.slug}.localhost").get("/static/core/css/app.css")
    assert not TenantTraffic.objects.exists()


def test_over_the_limit_the_workspace_answers_429(tenant_a):
    tenant_a.requests_per_minute = 3
    tenant_a.save()
    client = login(tenant_a)  # 1
    assert client.get("/").status_code == 200  # 2
    assert client.get("/").status_code == 200  # 3
    refused = client.get("/")
    assert refused.status_code == 429 and 1 <= int(refused["Retry-After"]) <= 60
    api = client.get("/api/v1/branches/")
    assert api.status_code == 429 and api.json()["error"]["code"] == "THROTTLED"
    row = _minute(tenant_a)
    assert (row.requests, row.throttled) == (5, 2)


def test_platform_default_and_no_limit(tenant_a, settings):
    settings.TENANT_REQUESTS_PER_MINUTE = 1
    assert traffic.limit_for(tenant_a) == 1
    client = login(tenant_a)
    assert client.get("/").status_code == 429
    tenant_a.requests_per_minute = 0  # this client: no limit
    tenant_a.save()
    assert traffic.limit_for(tenant_a) is None
    assert client.get("/").status_code == 200
    settings.TENANT_REQUESTS_PER_MINUTE = 0
    tenant_a.requests_per_minute = None
    assert traffic.limit_for(tenant_a) is None


def test_traffic_pages_and_changing_the_limit(console, tenant_a, tenant_b):
    login(tenant_a).get("/")
    for window in ("", "1h", "24h", "7d", "nonsense"):
        html = console.get(f"/traffic/?window={window}").content.decode()
        # b made no requests but is listed anyway, so it can be disabled from here
        assert tenant_a.name in html and tenant_b.name in html
        page = console.get(f"/clients/{tenant_a.pk}/traffic/?window={window}")
        assert page.status_code == 200 and 'dir="ltr">/login/<' in page.content.decode()
    assert console.get(f"/clients/{tenant_b.pk}/traffic/").status_code == 200

    response = console.post(f"/clients/{tenant_a.pk}/traffic/?window=1h",
                            {"requests_per_minute": "120"})
    assert response.status_code == 302 and response["Location"].endswith("?window=1h")
    tenant_a.refresh_from_db()
    assert tenant_a.requests_per_minute == 120
    event = PlatformEvent.objects.get(tenant=tenant_a, action="traffic.limit")
    assert event.detail == {"before": "", "after": "120"}

    console.post(f"/clients/{tenant_a.pk}/traffic/", {"requests_per_minute": ""})
    tenant_a.refresh_from_db()
    assert tenant_a.requests_per_minute is None
    console.post(f"/clients/{tenant_a.pk}/traffic/", {"requests_per_minute": "-1"})
    tenant_a.refresh_from_db()
    assert tenant_a.requests_per_minute is None


def test_traffic_pages_are_staff_only(tenant_a):
    client = Client(HTTP_HOST=HOST)
    assert "/login/" in client.get("/traffic/")["Location"]
    assert login(tenant_a).get("/traffic/").status_code == 404


def test_prune_traffic(tenant_a):
    old = timezone.now() - timedelta(days=40)
    traffic.record(tenant_a.id, traffic.current_minute(old), route="GET /", ms=5)
    traffic.record(tenant_a.id, traffic.current_minute(), route="GET /", ms=5)
    out = StringIO()
    call_command("prune_traffic", stdout=out)
    assert "deleted 1 minute rows, 0 route rows" in out.getvalue()
    assert TenantTraffic.objects.count() == 1
    assert TenantRouteTraffic.objects.count() == 2  # routes are kept 90 days


def test_live_parts(console, tenant_a):
    login(tenant_a).get("/")
    data = console.get("/traffic/live/?window=1h").json()
    assert data["window"] == "1h" and set(data["parts"]) == {"tiles", "chart", "clients"}
    assert tenant_a.name in data["parts"]["clients"]
    data = console.get(f"/clients/{tenant_a.pk}/traffic/live/").json()
    assert data["window"] == "24h"
    assert set(data["parts"]) == {"tiles", "chart", "routes", "summary", "access"}
    assert "/login/" in data["parts"]["routes"]
    assert "/login/" in Client(HTTP_HOST=HOST).get("/traffic/live/")["Location"]


def test_disable_and_enable_a_client(console, tenant_a):
    workspace = login(tenant_a)
    url = f"/clients/{tenant_a.pk}/access/"
    disabled = console.post(url, {"enabled": "0"}, HTTP_ACCEPT="application/json")
    assert disabled.status_code == 200 and disabled.json()["status"] == "suspended"
    tenant_a.refresh_from_db()
    assert tenant_a.status == TenantStatus.SUSPENDED
    assert workspace.get("/").status_code == 403  # cut off on the next request
    assert "Disabled" in console.get("/traffic/").content.decode()

    # Without JavaScript: a redirect back to `next`, never to another host.
    enabled = console.post(url, {"enabled": "1", "next": "/traffic/?window=1h"})
    assert enabled.status_code == 302 and enabled["Location"] == "/traffic/?window=1h"
    tenant_a.refresh_from_db()
    assert tenant_a.status == TenantStatus.ACTIVE
    assert workspace.get("/").status_code == 200
    evil = console.post(url, {"enabled": "1", "next": "https://evil.test/"})
    assert evil["Location"] == f"/clients/{tenant_a.pk}/traffic/"
    assert list(PlatformEvent.objects.filter(tenant=tenant_a, action="tenant.status")
                .order_by("id").values_list("detail", flat=True)) == [
        {"before": "active", "after": "suspended"}, {"before": "suspended", "after": "active"}]

    tenant_a.status = TenantStatus.ARCHIVED
    tenant_a.save()
    refused = console.post(url, {"enabled": "1"}, HTTP_ACCEPT="application/json")
    assert refused.status_code == 409
    tenant_a.refresh_from_db()
    assert tenant_a.status == TenantStatus.ARCHIVED
    assert console.get(url).status_code == 405
