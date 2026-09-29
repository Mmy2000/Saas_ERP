import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from apps.core.tenancy import tenant_context
from apps.iam.authz import Actor, build_actor
from apps.iam.catalog import WILDCARD, is_known_permission, permissions
from apps.iam.models import Role
from apps.iam.services import set_role_permissions
from apps.org.models import Branch
from conftest import login


class TestActor:
    def test_all_branches_scope(self):
        actor = Actor(user=None, membership=None, grants={"x.view": None})
        assert actor.can("x.view") and actor.can("x.view", branch=7)
        assert not actor.can("x.edit")

    def test_branch_scope(self):
        actor = Actor(user=None, membership=None, grants={"x.view": frozenset({1, 2})})
        assert actor.can("x.view", branch=1)
        assert not actor.can("x.view", branch=3)
        assert actor.can("x.view")  # tenant-wide question: granted somewhere
        assert actor.branch_ids("x.view") == frozenset({1, 2})

    def test_wildcard(self):
        actor = Actor(user=None, membership=None, grants={WILDCARD: None})
        assert actor.can("anything.at.all", branch=5)
        assert actor.branch_ids("anything") is None


@pytest.mark.django_db
class TestRoles:
    def test_system_roles_seeded_and_owner_assigned(self, tenant_a):
        with tenant_context(tenant_a.id):
            assert set(Role.objects.values_list("code", flat=True)) == {"owner", "manager",
                                                                      "viewer"}
            from apps.iam.models import Membership

            actor = build_actor(Membership.objects.get(username="owner"))
            assert actor.can("pricing.board.publish")

    def test_viewer_can_view_but_not_change(self, tenant_a, make_member):
        membership = make_member(tenant_a, "val", "viewer")
        with tenant_context(tenant_a.id):
            actor = build_actor(membership)
        assert actor.can("pricing.board.view")
        assert not actor.can("pricing.board.publish")
        assert not actor.can("parties.customer.view_pii")

    def test_branch_scoped_assignment(self, tenant_a, make_member):
        with tenant_context(tenant_a.id):
            second = Branch.objects.create(code=2, name="Second")
        membership = make_member(tenant_a, "sam", "manager", branches=[second])
        with tenant_context(tenant_a.id):
            actor = build_actor(membership)
            head = Branch.objects.get(code=1)
        assert actor.can("parties.customer.create", branch=second)
        assert not actor.can("parties.customer.create", branch=head)

    def test_owner_role_is_locked_and_unknown_codes_rejected(self, tenant_a):
        from apps.core.errors import ValidationError

        with tenant_context(tenant_a.id):
            with pytest.raises(ValidationError):
                set_role_permissions(Role.objects.get(code="owner"), ["org.branch.view"])
            with pytest.raises(ValidationError):
                set_role_permissions(Role.objects.get(code="viewer"), ["no.such.permission"])


def test_catalog_codes_are_well_formed():
    for code in permissions():
        assert code.count(".") >= 1 and code == code.lower()
    assert is_known_permission("pricing.board.publish")


def _api_views(patterns=None, prefix=""):
    for entry in patterns if patterns is not None else get_resolver("config.urls").url_patterns:
        if isinstance(entry, URLResolver):
            yield from _api_views(entry.url_patterns, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern) and (prefix + str(entry.pattern)).startswith("api/"):
            yield prefix + str(entry.pattern), entry.callback


def test_every_api_action_declares_a_permission():
    """§11.6: a view without `required_permissions` for an action is denied at runtime; this
    makes the omission a test failure instead of a silent 403."""
    missing = []
    for route, callback in _api_views():
        cls = getattr(callback, "cls", None)
        if cls is None:
            continue
        declared = getattr(cls, "required_permissions", {}) or {}
        actions = getattr(callback, "actions", None)
        if actions:
            keys = set(actions.values())
        else:
            keys = {m.upper() for m in cls.http_method_names
                    if hasattr(cls, m) and m not in ("options", "head")}
        for key in keys:
            code = declared.get(key)
            if code is None or not (is_known_permission(code) or code == "authenticated"):
                missing.append(f"{route} {key}")
    assert missing == []


@pytest.mark.django_db
class TestEnforcement:
    def test_viewer_cannot_publish_prices(self, tenant_a, make_member):
        make_member(tenant_a, "val", "viewer")
        client = login(tenant_a, "val")
        with tenant_context(tenant_a.id):
            from apps.catalog.models import Karat

            karat = Karat.objects.get(code=21).pk
        response = client.post("/api/v1/pricing/boards/",
                               {"reference": {"karat": karat, "sell_price_per_g": "4000"}},
                               content_type="application/json")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert client.get("/api/v1/pricing/boards/").status_code == 200

    def test_web_pages_respect_permissions(self, tenant_a, make_member):
        make_member(tenant_a, "val", "viewer")
        client = login(tenant_a, "val")
        assert client.get("/customers/").status_code == 200
        assert client.get("/customers/new/").status_code == 403

    def test_navigation_hides_what_you_cannot_open(self, tenant_a, make_member):
        with tenant_context(tenant_a.id):
            role = Role.objects.create(code="prices-only", name="Prices only")
            set_role_permissions(role, ["pricing.board.view"])
        member = make_member(tenant_a, "pat", "viewer")
        with tenant_context(tenant_a.id):
            member.roles.all().delete()
            from apps.iam.services import assign_role

            assign_role(member, role)
        html = login(tenant_a, "pat").get("/", HTTP_ACCEPT_LANGUAGE="en").content.decode()
        assert 'href="/pricing/gold/"' in html
        assert 'href="/customers/"' not in html
        assert 'href="/branches/"' not in html
