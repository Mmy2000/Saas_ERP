"""The platform console (platform hosts only, platform staff only)."""

import pytest
from django.test import Client

from apps.core.errors import DomainError
from apps.core.tenancy import tenant_context
from apps.iam.models import User
from apps.platform.tenants.models import PlatformEvent, Tenant, TenantStatus
from conftest import PASSWORD, login

pytestmark = pytest.mark.django_db
HOST = "admin.localhost"


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="ops@gweb.test", password=PASSWORD,
                                    display_name="Ops", is_platform_staff=True)


@pytest.fixture
def console(staff):
    client = Client(HTTP_HOST=HOST)
    client.cookies["django_language"] = "en"
    response = client.post("/login/", {"username": staff.email, "password": PASSWORD})
    assert response.status_code == 302, response.content
    return client


def test_only_platform_staff_get_in(tenant_a):
    client = Client(HTTP_HOST=HOST)
    client.cookies["django_language"] = "en"
    assert client.get("/").status_code == 302  # to the login page
    assert "/login/" in client.get("/clients/")["Location"]
    User.objects.create_user(email="clerk@alpha.test", password=PASSWORD, display_name="Clerk")
    refused = client.post("/login/", {"username": "clerk@alpha.test", "password": PASSWORD})
    assert refused.status_code == 200
    assert "cannot use the platform console" in refused.content.decode()
    # The console is not served on a client's host.
    assert login(tenant_a).get("/clients/").status_code == 404


def test_overview_and_pages(console, tenant_a):
    html = console.get("/").content.decode()
    assert "Platform overview" in html and tenant_a.name in html
    for path in ("/", "/clients/", "/clients/?status=active", "/clients/?q=alp",
                 "/clients/new/", f"/clients/{tenant_a.pk}/", "/activity/",
                 f"/activity/?tenant={tenant_a.pk}"):
        assert console.get(path).status_code == 200, path


def test_create_a_client(console):
    response = console.post("/clients/new/", {
        "name": "Nile Gold", "slug": "nile", "locale": "ar", "country": "EG",
        "functional_currency": "EGP", "timezone": "Africa/Cairo", "fineness_24k": "999.9",
        "owner_display_name": "Sara", "owner_username": "sara",
        "owner_email": "sara@nile.test", "owner_password": PASSWORD, "plan": "trial",
        "max_branches": "2", "max_users": "3", "contact_name": "Sara Adel",
        "contact_phone": "01000000000"})
    tenant = Tenant.objects.get(slug="nile")
    assert response.status_code == 302 and response["Location"] == f"/clients/{tenant.pk}/"
    assert (tenant.status, tenant.plan, tenant.max_branches, tenant.max_users) == (
        TenantStatus.ACTIVE, "trial", 2, 3)
    assert tenant.domains.get().domain == "nile.localhost"
    assert PlatformEvent.objects.filter(tenant=tenant, action="tenant.created").exists()
    # The owner signs in to the new workspace right away.
    assert login(tenant, username="sara").get("/").status_code == 200

    taken = console.post("/clients/new/", {"name": "Again", "slug": "nile"})
    assert taken.status_code == 200 and "This address is taken" in taken.content.decode()


def test_limits_are_enforced_in_the_workspace(console, tenant_a):
    from apps.iam.services import MemberInput, create_member
    from apps.org.services import BranchInput, create_branch

    console.post(f"/clients/{tenant_a.pk}/settings/", {
        "name": tenant_a.name, "plan": "standard", "max_branches": "1", "max_users": "1"})
    tenant_a.refresh_from_db()
    assert (tenant_a.max_branches, tenant_a.max_users) == (1, 1)
    with tenant_context(tenant_a.id):
        with pytest.raises(DomainError) as branch_error:
            create_branch(BranchInput(code=2, name="Maadi"))
        assert branch_error.value.code == "PLAN_BRANCH_LIMIT"
        with pytest.raises(DomainError) as user_error:
            create_member(MemberInput(username="clerk", display_name="Clerk",
                                      email="clerk@alpha.test"), PASSWORD)
        assert user_error.value.code == "PLAN_USER_LIMIT"

    console.post(f"/clients/{tenant_a.pk}/settings/", {"name": tenant_a.name, "plan": "standard"})
    with tenant_context(tenant_a.id):
        create_branch(BranchInput(code=2, name="Maadi"))  # no limit any more


def test_suspend_domains_and_settings(console, tenant_a):
    url = f"/clients/{tenant_a.pk}/"
    console.post(url + "status/", {"status": "suspended"})
    tenant_a.refresh_from_db()
    assert tenant_a.status == TenantStatus.SUSPENDED
    assert Client(HTTP_HOST="alpha.localhost").get("/login/").status_code == 403
    console.post(url + "status/", {"status": "archived"})
    console.post(url + "status/", {"status": "active"})
    tenant_a.refresh_from_db()
    assert tenant_a.status == TenantStatus.ACTIVE
    console.post(url + "status/", {"status": "purged"})  # not offered: ignored
    tenant_a.refresh_from_db()
    assert tenant_a.status == TenantStatus.ACTIVE

    console.post(url + "domains/", {"domain": "ERP.Alpha-Gold.localhost"})
    domain = tenant_a.domains.get(domain="erp.alpha-gold.localhost")
    assert Client(HTTP_HOST="erp.alpha-gold.localhost").get("/login/").status_code == 200
    console.post(url + f"domains/{domain.pk}/remove/")
    assert not tenant_a.domains.filter(pk=domain.pk).exists()
    primary = tenant_a.domains.get(is_primary=True)
    console.post(url + f"domains/{primary.pk}/remove/")  # the main address stays
    assert tenant_a.domains.filter(pk=primary.pk).exists()

    console.post(url + "profile/", {"display_name": "Alpha Jewellers", "locale": "en",
                                    "country": "EG", "timezone": "Africa/Cairo"})
    with tenant_context(tenant_a.id):
        from apps.org.models import TenantProfile

        profile = TenantProfile.objects.get()
        assert (profile.display_name, profile.locale) == ("Alpha Jewellers", "en")
    actions = list(PlatformEvent.objects.filter(tenant=tenant_a).values_list("action", flat=True))
    assert {"tenant.status", "domain.added", "domain.removed", "profile.updated"} <= set(actions)
