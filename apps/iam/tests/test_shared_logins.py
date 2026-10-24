"""A login used by several workspaces, or by Gweb's staff, can't be taken over from one of them
(ADR-022): no workspace changes its password or email, and a new workspace is added to an
existing login only when asked for."""

import pytest

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.iam.models import Membership, Role, User
from apps.iam.services import assign_role
from apps.platform.tenants.models import Tenant
from apps.platform.tenants.services import ProvisionTenantCommand, provision_tenant
from conftest import PASSWORD, login

pytestmark = pytest.mark.django_db
NEW_PASSWORD = "another-long-passphrase"


def _post(client, url, data):
    return client.post(url, data, content_type="application/json")


def _join(tenant, user, username):
    with tenant_context(tenant.id):
        membership = Membership.objects.create(user=user, username=username)
        assign_role(membership, Role.objects.get(code="viewer"))
        return membership


def _count(user) -> int:
    return User.objects.get(pk=user.pk).membership_count


def test_the_database_counts_each_logins_workspaces(tenant_a, tenant_b, make_member):
    nour = make_member(tenant_a, "nour").user
    assert _count(nour) == 1
    second = _join(tenant_b, nour, "nour")
    assert _count(nour) == 2
    with tenant_context(tenant_b.id):
        second.roles.all().delete()
        second.delete()
    assert _count(nour) == 1


def test_a_shared_login_keeps_its_password_and_email(tenant_a, tenant_b, make_member):
    """Bravo's owner must not be able to reset Nour's password and sign in to Alpha as her."""
    member = make_member(tenant_a, "nour")
    in_b = _join(tenant_b, member.user, "nour")
    owner_b = login(tenant_b)
    refused = _post(owner_b, f"/api/v1/iam/members/{in_b.pk}/set-password/",
                    {"password": NEW_PASSWORD})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "IAM_SHARED_LOGIN"
    with tenant_context(tenant_b.id):
        viewer = Role.objects.get(code="viewer").pk
    changed = owner_b.put(f"/api/v1/iam/members/{in_b.pk}/", {
        "username": "nour", "display_name": "Nour", "email": "mine@bravo.test",
        "roles": [{"role": viewer, "all_branches": True}]}, content_type="application/json")
    assert changed.status_code == 400 and "email" in changed.json()["error"]["fields"]
    assert login(tenant_a, "nour").get("/").status_code == 200  # still her own password

    # A login of one workspace only is still managed there.
    solo = make_member(tenant_b, "sami")
    assert _post(owner_b, f"/api/v1/iam/members/{solo.pk}/set-password/",
                 {"password": NEW_PASSWORD}).status_code == 204


def test_a_staff_login_is_never_changed_by_a_workspace(tenant_a):
    staff = User.objects.create_user(email="ops@gweb.test", password=PASSWORD,
                                     is_platform_staff=True)
    member = _join(tenant_a, staff, "ops")
    refused = _post(login(tenant_a), f"/api/v1/iam/members/{member.pk}/set-password/",
                    {"password": NEW_PASSWORD})
    assert refused.status_code == 400


def test_a_phone_taken_elsewhere_is_a_field_error(tenant_a, tenant_b, make_member):
    with tenant_context(tenant_b.id):
        User.objects.filter(pk=Membership.objects.get(username="owner").user_id).update(
            phone="01000000001")
    with tenant_context(tenant_a.id):
        viewer = Role.objects.get(code="viewer").pk
    response = _post(login(tenant_a), "/api/v1/iam/members/", {
        "username": "hala", "display_name": "Hala", "password": NEW_PASSWORD,
        "phone": "01000000001", "roles": [{"role": viewer, "all_branches": True}]})
    assert response.status_code == 400 and "phone" in response.json()["error"]["fields"]


def _provision(slug, email, **kw):
    return provision_tenant(ProvisionTenantCommand(
        slug=slug, name=slug.title(), domain=f"{slug}.localhost", owner_username="owner",
        owner_email=email, **kw))


def test_a_new_workspace_joins_an_existing_login_only_when_asked(tenant_a):
    """Someone who created a member with a future client's email (and so knows its password)
    must not become that client's owner when staff open the workspace."""
    existing = "owner@alpha.test"
    for kw, code in (({"owner_password": None}, "TENANT_OWNER_EXISTS"),
                     ({"owner_password": PASSWORD, "link_existing_owner": True},
                      "TENANT_OWNER_PASSWORD")):
        with pytest.raises(ValidationError) as refused:
            _provision("cairo", existing, **kw)
        assert refused.value.code == code
    assert not Tenant.objects.filter(slug="cairo").exists()

    _provision("cairo", existing, owner_password=None, link_existing_owner=True)
    assert User.objects.get(email=existing).membership_count == 2

    User.objects.create_user(email="ops@gweb.test", password=PASSWORD, is_platform_staff=True)
    with pytest.raises(ValidationError) as refused:
        _provision("giza", "ops@gweb.test", owner_password=None, link_existing_owner=True)
    assert refused.value.code == "TENANT_OWNER_IS_STAFF"
