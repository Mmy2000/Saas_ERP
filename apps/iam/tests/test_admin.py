"""User, role and branch administration, with the guard rails of apps.iam.services."""

import pytest

from apps.core.tenancy import tenant_context
from apps.iam.models import Membership, Role
from apps.iam.services import set_role_permissions
from apps.org.models import Branch
from conftest import PASSWORD, login

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "another-long-passphrase"


def _role(tenant, code):
    with tenant_context(tenant.id):
        return Role.objects.get(code=code).pk


def _member_payload(tenant, username="sara", roles=None, **extra):
    return {"username": username, "display_name": username.title(), "password": NEW_PASSWORD,
            "roles": roles or [{"role": _role(tenant, "viewer"), "all_branches": True}], **extra}


def _post(client, url, data):
    return client.post(url, data, content_type="application/json")


class TestMembers:
    def test_owner_adds_a_member_who_can_sign_in(self, tenant_a):
        with tenant_context(tenant_a.id):
            second = Branch.objects.create(code=2, name="Second")
        client = login(tenant_a)
        response = _post(client, "/api/v1/iam/members/", _member_payload(
            tenant_a, roles=[{"role": _role(tenant_a, "manager"), "all_branches": False,
                              "branches": [second.pk]}]))
        assert response.status_code == 201, response.content
        roles = response.json()["roles"]
        assert roles[0]["all_branches"] is False and roles[0]["branches"] == [second.pk]
        assert login(tenant_a, "sara", password=NEW_PASSWORD).get("/").status_code == 200

    def test_username_unique_and_email_not_shared(self, tenant_a, tenant_b):
        client = login(tenant_a)
        response = _post(client, "/api/v1/iam/members/", _member_payload(tenant_a, "owner"))
        assert "username" in response.json()["error"]["fields"]
        response = _post(client, "/api/v1/iam/members/",
                         _member_payload(tenant_a, email="owner@bravo.test"))
        assert "email" in response.json()["error"]["fields"]

    def test_weak_password_rejected(self, tenant_a):
        payload = _member_payload(tenant_a, password="123")
        response = _post(login(tenant_a), "/api/v1/iam/members/", payload)
        assert "password" in response.json()["error"]["fields"]

    def test_viewer_cannot_manage_users(self, tenant_a, make_member):
        make_member(tenant_a, "val", "viewer")
        response = _post(login(tenant_a, "val"), "/api/v1/iam/members/",
                         _member_payload(tenant_a))
        assert response.status_code == 403

    def test_no_privilege_escalation(self, tenant_a, make_member):
        with tenant_context(tenant_a.id):
            hr = Role.objects.create(code="hr", name="HR")
            set_role_permissions(hr, ["admin.users.view", "admin.users.manage"])
        member = make_member(tenant_a, "hana", "viewer")
        with tenant_context(tenant_a.id):
            from apps.iam.services import assign_role

            member.roles.all().delete()
            assign_role(member, hr)
        client = login(tenant_a, "hana")
        for role in ("owner", "manager"):
            grant = [{"role": _role(tenant_a, role), "all_branches": True}]
            response = _post(client, "/api/v1/iam/members/",
                             _member_payload(tenant_a, f"x{role}", roles=grant))
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "IAM_ESCALATION"
        # Granting a role within their own powers is fine.
        ok = _post(client, "/api/v1/iam/members/", _member_payload(
            tenant_a, "helper", roles=[{"role": hr.pk, "all_branches": True}]))
        assert ok.status_code == 201

    def test_last_owner_is_kept(self, tenant_a):
        client = login(tenant_a)
        with tenant_context(tenant_a.id):
            owner = Membership.objects.get(username="owner")
        response = client.put(f"/api/v1/iam/members/{owner.pk}/", _member_payload(
            tenant_a, "owner", roles=[{"role": _role(tenant_a, "viewer"), "all_branches": True}]),
            content_type="application/json")
        assert response.json()["error"]["code"] == "IAM_LAST_OWNER"

    def test_cannot_suspend_yourself_but_others_yes(self, tenant_a, make_member):
        client = login(tenant_a)
        with tenant_context(tenant_a.id):
            owner = Membership.objects.get(username="owner")
        assert _post(client, f"/api/v1/iam/members/{owner.pk}/suspend/", {}).json()[
            "error"]["code"] == "IAM_SELF_SUSPEND"
        other = make_member(tenant_a, "val", "viewer")
        assert _post(client, f"/api/v1/iam/members/{other.pk}/suspend/", {}).json()[
            "status"] == "suspended"
        blocked = Client_for(tenant_a).post("/login/", {"username": "val", "password": PASSWORD})
        assert blocked.status_code == 200  # suspended members cannot sign in

    def test_new_password_ends_existing_sessions(self, tenant_a, make_member):
        member = make_member(tenant_a, "val", "viewer")
        session = login(tenant_a, "val")
        assert session.get("/").status_code == 200
        response = _post(login(tenant_a), f"/api/v1/iam/members/{member.pk}/set-password/",
                         {"password": NEW_PASSWORD})
        assert response.status_code == 204
        assert session.get("/").status_code == 302  # signed out


class TestRoles:
    def test_create_update_delete(self, tenant_a):
        client = login(tenant_a)
        created = _post(client, "/api/v1/iam/roles/", {
            "name": "Cashier", "permissions": ["pricing.board.view", "parties.customer.view"]})
        assert created.status_code == 201
        role_id = created.json()["id"]
        updated = client.put(f"/api/v1/iam/roles/{role_id}/", {
            "name": "Cashier", "permissions": ["pricing.board.view"]},
            content_type="application/json")
        assert updated.json()["permissions"] == ["pricing.board.view"]
        assert client.delete(f"/api/v1/iam/roles/{role_id}/").status_code == 204

    def test_owner_role_locked_and_builtins_kept(self, tenant_a):
        client = login(tenant_a)
        owner = _role(tenant_a, "owner")
        response = client.put(f"/api/v1/iam/roles/{owner}/", {"name": "x", "permissions": []},
                              content_type="application/json")
        assert response.json()["error"]["code"] == "IAM_OWNER_LOCKED"
        response = client.delete(f"/api/v1/iam/roles/{_role(tenant_a, 'viewer')}/")
        assert response.json()["error"]["code"] == "IAM_SYSTEM_ROLE"

    def test_role_in_use_cannot_be_deleted(self, tenant_a, make_member):
        client = login(tenant_a)
        created = _post(client, "/api/v1/iam/roles/", {"name": "Temp", "permissions": []})
        role_id = created.json()["id"]
        member = make_member(tenant_a, "val", "viewer")
        with tenant_context(tenant_a.id):
            from apps.iam.services import assign_role

            assign_role(member, Role.objects.get(pk=role_id))
        assert client.delete(f"/api/v1/iam/roles/{role_id}/").json()["error"]["code"] == \
            "IAM_ROLE_IN_USE"


class TestBranches:
    def test_create_and_move_head_office(self, tenant_a):
        client = login(tenant_a)
        created = _post(client, "/api/v1/org/branches/",
                        {"code": 2, "name": "Nasr City", "is_head_office": True})
        assert created.status_code == 201, created.content
        with tenant_context(tenant_a.id):
            heads = list(Branch.objects.filter(is_head_office=True).values_list("code", flat=True))
        assert heads == [2]

    def test_duplicate_code_and_head_office_deactivation(self, tenant_a):
        client = login(tenant_a)
        duplicate = _post(client, "/api/v1/org/branches/", {"code": 1, "name": "Again"})
        assert duplicate.status_code == 400
        with tenant_context(tenant_a.id):
            head = Branch.objects.get(code=1)
        response = _post(client, f"/api/v1/org/branches/{head.pk}/deactivate/", {})
        assert response.json()["error"]["code"] == "ORG_HEAD_OFFICE_ACTIVE"


def Client_for(tenant):  # noqa: N802 - small local helper
    from django.test import Client

    return Client(HTTP_HOST=f"{tenant.slug}.localhost")
