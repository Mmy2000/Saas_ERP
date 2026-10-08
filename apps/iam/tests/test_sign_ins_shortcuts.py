"""The sign-in history (legacy AuthLog, plus failed attempts) and pinned screens (legacy
ShortCuts)."""

import pytest
from django.test import Client

from apps.core.tenancy import tenant_context
from apps.iam.authz import build_actor
from apps.iam.models import Membership, Shortcut, SignInEvent
from apps.iam.shortcuts import MAX_SHORTCUTS, pin, pinned, unpin
from apps.iam.signins import device
from conftest import PASSWORD, login

pytestmark = pytest.mark.django_db

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/129.0 Safari/537.36")


def events(tenant):
    with tenant_context(tenant.id):
        return list(SignInEvent.objects.order_by("id").values_list(
            "outcome", "username", "membership__username", "ip"))


def test_sign_ins_failures_and_sign_outs_are_recorded(tenant_a, tenant_b):
    client = Client(HTTP_HOST="alpha.localhost", HTTP_USER_AGENT=CHROME,
                    REMOTE_ADDR="10.0.0.7")
    assert client.post("/login/", {"username": "owner", "password": "wrong"}).status_code == 200
    assert client.post("/login/", {"username": "ghost", "password": "x"}).status_code == 200
    assert client.post("/login/", {"username": "owner", "password": PASSWORD}).status_code == 302
    client.post("/logout/")
    assert events(tenant_a) == [("failed", "owner", "owner", "10.0.0.7"),
                                ("failed", "ghost", None, "10.0.0.7"),
                                ("in", "owner", "owner", "10.0.0.7"),
                                ("out", "owner", "owner", "10.0.0.7")]
    assert events(tenant_b) == []  # each workspace keeps its own
    with tenant_context(tenant_a.id):
        assert SignInEvent.objects.first().user_agent == CHROME
    assert device(CHROME) == "Chrome · Windows"
    assert device("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1") \
        == "Safari · iPhone"
    assert device("curl/8.4") == "curl/8.4"


def test_sign_ins_page_is_for_owners(tenant_a, make_member):
    make_member(tenant_a, "mona", "manager")
    owner = login(tenant_a, language="en")
    Client(HTTP_HOST="alpha.localhost").post("/login/", {"username": "mona", "password": "no"})
    page = owner.get("/settings/sign-ins/")
    assert page.status_code == 200
    text = page.content.decode()
    assert "Wrong name or password" in text and "1 failed sign-in in the last 24 hours" in text
    assert owner.get("/settings/sign-ins/?outcome=failed&ip=127").status_code == 200
    assert "Last sign-in" in owner.get("/settings/users/").content.decode()
    manager = login(tenant_a, "mona")
    assert manager.get("/settings/sign-ins/").status_code == 403  # admin.* is the owner's


def test_pinning_screens(tenant_a, make_member):
    viewer = make_member(tenant_a, "vera", "viewer")
    with tenant_context(tenant_a.id):
        owner = build_actor(Membership.objects.get(username="owner"))
        pin(owner, "stock")
        pin(owner, "customers")
        pin(owner, "stock")  # twice: still once
        assert [s["screen"] for s in pinned(owner)] == ["stock", "customers"]
        unpin(owner, "stock")
        assert [s["screen"] for s in pinned(owner)] == ["customers"]
        from apps.core.errors import ValidationError

        with pytest.raises(ValidationError):
            pin(owner, "no-such-screen")
        with pytest.raises(ValidationError):
            pin(owner, "home")  # the dashboard is always there
        with pytest.raises(ValidationError):  # not allowed to open it
            pin(build_actor(viewer), "sale-new")
        for name in ("stock", "sales", "repairs", "invoices", "scrap", "journal", "accounts",
                     "karats", "categories", "branches", "users"):
            pin(owner, name)
        assert Shortcut.objects.filter(membership=owner.membership).count() == MAX_SHORTCUTS
        with pytest.raises(ValidationError):
            pin(owner, "roles")


def test_shortcut_endpoint_star_sidebar_and_dashboard(tenant_a):
    client = login(tenant_a, language="en")
    page = client.get("/stock/").content.decode()
    assert 'data-pin="stock"' in page and 'aria-pressed="false"' in page
    made = client.post("/api/v1/iam/shortcuts/", {"screen": "stock", "pinned": True},
                       content_type="application/json")
    assert made.status_code == 200 and made.json()["results"][0]["screen"] == "stock"
    page = client.get("/stock/").content.decode()
    assert 'data-screen="stock"' in page  # in the sidebar's Pinned list
    home = client.get("/").content.decode()
    assert "Your shortcuts" in home and 'data-pin="home"' not in home
    bad = client.post("/api/v1/iam/shortcuts/", {"screen": "nope", "pinned": True},
                      content_type="application/json")
    assert bad.status_code == 400
    gone = client.post("/api/v1/iam/shortcuts/", {"screen": "stock", "pinned": False},
                       content_type="application/json")
    assert gone.json() == {"pinned": False, "results": []}
