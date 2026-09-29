import pytest
from django.core.cache import cache
from django.test import Client

from apps.core.tenancy import tenant_context
from apps.platform.tenants.services import ProvisionTenantCommand, provision_tenant

PASSWORD = "correct-horse-battery-staple"


def pytest_addoption(parser):
    parser.addoption("--e2e", action="store_true", help="also run browser tests (tests/e2e)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--e2e"):
        return
    skip = pytest.mark.skip(reason="browser test: run with --e2e")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _clear_cache():
    # The host → tenant cache would otherwise outlive the rolled-back rows of earlier tests.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def make_tenant(db):
    def _make(slug: str, *, owner_username: str = "owner", **overrides):
        cmd = ProvisionTenantCommand(
            slug=slug,
            name=overrides.pop("name", slug.title()),
            domain=overrides.pop("domain", f"{slug}.localhost"),
            owner_username=owner_username,
            owner_password=PASSWORD,
            owner_email=overrides.pop("owner_email", f"owner@{slug}.test"),
            **overrides,
        )
        return provision_tenant(cmd)

    return _make


@pytest.fixture
def tenant_a(make_tenant):
    return make_tenant("alpha")


@pytest.fixture
def tenant_b(make_tenant):
    return make_tenant("bravo")


@pytest.fixture
def make_member(db):
    """Create a member of `tenant` holding the system role `role` (owner/manager/viewer)."""
    from apps.iam.models import Membership, Role, User
    from apps.iam.services import assign_role

    def _make(tenant, username: str, role: str = "viewer", *, branches=None):
        with tenant_context(tenant.id):
            user = User.objects.create_user(email=f"{username}@{tenant.slug}.test",
                                            password=PASSWORD, display_name=username.title())
            membership = Membership.objects.create(user=user, username=username)
            assign_role(membership, Role.objects.get(code=role), branches=branches)
            return membership

    return _make


def login(tenant, username: str = "owner", *, language: str | None = None,
          password: str = PASSWORD) -> Client:
    """A test client signed in as `username` on the tenant's host."""
    client = Client(HTTP_HOST=f"{tenant.slug}.localhost")
    if language:
        client.cookies["django_language"] = language
    response = client.post("/login/", {"username": username, "password": password})
    assert response.status_code == 302, "login failed"
    return client
