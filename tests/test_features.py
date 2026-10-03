"""Features switched on and off per client and per plan from the console."""

import pytest
from django.test import Client

from apps.iam.models import User
from apps.platform.tenants import features
from apps.platform.tenants.models import PlatformEvent, TenantFeature
from conftest import PASSWORD, login

pytestmark = pytest.mark.django_db


@pytest.fixture
def console(db):
    User.objects.create_user(email="ops@gweb.test", password=PASSWORD, display_name="Ops",
                             is_platform_staff=True)
    client = Client(HTTP_HOST="admin.localhost")
    client.cookies["django_language"] = "en"
    assert client.post("/login/", {"username": "ops@gweb.test", "password": PASSWORD}
                       ).status_code == 302
    return client


def _workspace(tenant):
    client = login(tenant)
    client.cookies["django_language"] = "en"
    return client


def test_every_feature_starts_on(tenant_a):
    assert features.enabled_keys(tenant_a.pk) == {f.key for f in features.FEATURES}
    html = _workspace(tenant_a).get("/").content.decode()
    assert 'href="/repairs/"' in html and 'href="/sales/wholesale/"' in html


def test_switching_a_feature_off_for_one_client(console, tenant_a, tenant_b):
    response = console.post(f"/clients/{tenant_a.pk}/features/", {"key": "repairs",
                                                                   "state": "off"})
    assert response.status_code == 302 and response["Location"].endswith("#f-repairs")
    assert PlatformEvent.objects.filter(tenant=tenant_a, action="feature.client").exists()

    owner = _workspace(tenant_a)  # the Owner (every permission) loses it too
    assert 'href="/repairs/"' not in owner.get("/").content.decode()
    page = owner.get("/repairs/")
    assert page.status_code == 403 and "not in your plan" in page.content.decode()
    api = owner.get("/api/v1/repairs/")
    assert api.status_code == 403
    assert "not available in your plan" in api.json()["error"]["message"]
    # Everything else still works, and another client is untouched.
    assert owner.get("/sales/").status_code == 200
    assert _workspace(tenant_b).get("/repairs/").status_code == 200

    console.post(f"/clients/{tenant_a.pk}/features/", {"key": "repairs", "state": "default"})
    assert not TenantFeature.objects.filter(tenant=tenant_a).exists()
    assert _workspace(tenant_a).get("/repairs/").status_code == 200


def test_plan_defaults_and_client_exceptions(console, tenant_a, tenant_b):
    tenant_b.plan_id = "enterprise"
    tenant_b.save()
    page = console.get("/features/")
    assert page.status_code == 200 and "Workshops and work orders" in page.content.decode()

    console.post("/features/plan/", {"plan": tenant_a.plan_id, "key": "manufacturing",
                                     "enabled": "0"})
    assert _workspace(tenant_a).get("/manufacturing/").status_code == 403
    assert _workspace(tenant_b).get("/manufacturing/").status_code == 200  # other plan

    # A client-level "on" beats the plan; moving to another plan applies that plan.
    console.post(f"/clients/{tenant_a.pk}/features/", {"key": "manufacturing", "state": "on"})
    assert _workspace(tenant_a).get("/manufacturing/").status_code == 200
    console.post(f"/clients/{tenant_a.pk}/features/", {"key": "manufacturing",
                                                       "state": "default"})
    tenant_a.plan_id = "enterprise"
    tenant_a.save()
    assert _workspace(tenant_a).get("/manufacturing/").status_code == 200

    html = console.get(f"/clients/{tenant_a.pk}/").content.decode()
    assert 'id="f-manufacturing"' in html and "From the plan" in html
    assert PlatformEvent.objects.filter(action="feature.plan").exists()


def test_screens_still_render_with_most_features_off(console, tenant_a):
    for feature in features.FEATURES:
        TenantFeature.objects.create(tenant=tenant_a, key=feature.key, enabled=False)
    features.forget(tenant_a.pk)
    owner = _workspace(tenant_a)
    for path in ("/", "/sales/", "/sales/new/", "/stock/", "/purchasing/", "/customers/",
                 "/settlements/", "/settlements/new/", "/pricing/gold/"):
        assert owner.get(path).status_code == 200, path
    html = owner.get("/").content.decode()
    for hidden in ("/repairs/", "/treasury/", "/accounting/journal/", "/reports/"):
        assert f'href="{hidden}"' not in html


def test_bad_input_and_staff_only(console, tenant_a):
    assert console.post(f"/clients/{tenant_a.pk}/features/",
                        {"key": "nope", "state": "on"}).status_code == 404
    assert console.post("/features/plan/", {"plan": "gold", "key": "repairs",
                                            "enabled": "1"}).status_code == 404
    assert "/login/" in Client(HTTP_HOST="admin.localhost").get("/features/")["Location"]


def test_every_feature_owns_real_permissions_and_none_overlap():
    from apps.iam.catalog import permissions

    codes = list(permissions())
    for feature in features.FEATURES:
        for prefix in feature.permissions:
            assert any(code == prefix or code.startswith(prefix) for code in codes), (
                f"{feature.key}: no permission matches {prefix!r}")
    owners = {}
    for code in codes:
        owning = [f.key for f in features.FEATURES if f.owns(code)]
        assert len(owning) <= 1, f"{code} belongs to {owning}"
        owners[code] = owning
    assert len({f.key for f in features.FEATURES}) == len(features.FEATURES)
