"""Host resolution, login, session binding and API scoping (§5.4, §23.3 "API sweep")."""

import pytest
from django.test import Client

from apps.core.tenancy import tenant_context
from apps.org.models import Branch
from apps.platform.tenants.models import TenantStatus
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

A_HOST = "alpha.localhost"
B_HOST = "bravo.localhost"


def _login(client, host, username="owner", password=PASSWORD):
    return client.post("/login/", {"username": username, "password": password}, HTTP_HOST=host)


def test_unknown_host_is_404(tenant_a):
    assert Client().get("/login/", HTTP_HOST="nobody.localhost").status_code == 404


def test_suspended_tenant_is_403(tenant_a):
    tenant_a.status = TenantStatus.SUSPENDED
    tenant_a.save()
    assert Client().get("/login/", HTTP_HOST=A_HOST).status_code == 403


def test_admin_is_not_served_on_tenant_hosts(tenant_a):
    assert Client().get("/admin/", HTTP_HOST=A_HOST).status_code == 404


def test_login_with_tenant_username(tenant_a):
    client = Client()
    response = _login(client, A_HOST)
    assert response.status_code == 302
    home = client.get("/", HTTP_HOST=A_HOST)
    assert home.status_code == 200
    assert "Alpha" in home.content.decode()


def test_login_with_email_of_a_member(tenant_a):
    client = Client()
    assert _login(client, A_HOST, username="owner@alpha.test").status_code == 302
    assert client.get("/", HTTP_HOST=A_HOST).status_code == 200


@pytest.mark.parametrize("username", ["owner", "owner@alpha.test"])
def test_credentials_of_a_do_not_work_on_another_tenant(tenant_a, make_tenant, username):
    make_tenant("charlie", owner_username="boss")
    client = Client()
    response = _login(client, "charlie.localhost", username=username)
    assert response.status_code == 200  # form re-rendered with an error
    assert "_auth_user_id" not in client.session


def test_session_from_a_is_not_honoured_on_b(tenant_a, tenant_b):
    client = Client()
    _login(client, A_HOST)
    # The test client sends cookies to every host; the server must still refuse.
    response = client.get("/", HTTP_HOST=B_HOST)
    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_api_lists_only_own_branches(tenant_a, tenant_b):
    with tenant_context(tenant_b.id):
        b_branch = Branch.objects.create(code=7, name="Bravo seven")

    client = Client()
    _login(client, A_HOST)
    response = client.get("/api/v1/org/branches/", HTTP_HOST=A_HOST)
    assert response.status_code == 200
    assert [b["code"] for b in response.json()["results"]] == [1]

    foreign = client.get(f"/api/v1/org/branches/{b_branch.id}/", HTTP_HOST=A_HOST)
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "NOT_FOUND"


def test_api_requires_authentication(tenant_a):
    response = Client().get("/api/v1/iam/me/", HTTP_HOST=A_HOST)
    assert response.status_code == 403
    assert response.json()["error"]["code"] in {"NOT_AUTHENTICATED", "PERMISSION_DENIED"}


def test_me(tenant_a):
    client = Client()
    _login(client, A_HOST)
    body = client.get("/api/v1/iam/me/", HTTP_HOST=A_HOST).json()
    assert body["tenant"] == "alpha"
    assert body["username"] == "owner"


def test_request_id_is_echoed(tenant_a):
    response = Client().get("/login/", HTTP_HOST=A_HOST, HTTP_X_REQUEST_ID="abc-123")
    assert response["X-Request-ID"] == "abc-123"
