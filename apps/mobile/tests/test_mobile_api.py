"""The mobile app's API, at Gweb's addresses and in Gweb's shapes (D:\\work\\Gold-inventory is not
changed): sign-in and tokens, stock, and the stocktake."""

import base64
import json
import time as clock

import pytest
from django.test import Client
from django.utils import timezone, translation

from apps.core.tenancy import tenant_context
from apps.inventory.tests.test_transfers_stocktakes import Stock
from apps.mobile.models import MobileDevice
from apps.platform.tenants import features
from apps.platform.tenants.models import TenantFeature
from apps.printing.tests.test_pdf import chromium  # noqa: F401  (fixture)
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _english():
    with translation.override("en"):
        yield


def switch(tenant, on=True):
    TenantFeature.objects.update_or_create(tenant=tenant, key="mobile_app",
                                           defaults={"enabled": on})
    features.forget(tenant.pk)


@pytest.fixture
def shop(tenant_a):
    switch(tenant_a)
    with tenant_context(tenant_a.id):
        stock = Stock()
    stock.tenant = tenant_a
    return stock


class App:
    """The phone: JSON in and out, the Bearer token once signed in."""

    def __init__(self, tenant, host=None):
        self.client = Client(HTTP_HOST=host or f"{tenant.slug}.localhost",
                             HTTP_USER_AGENT="okhttp/4.9 (Android 14)")
        self.client.cookies["django_language"] = "en"  # the assertions are in English
        self.access = self.refresh = None

    def call(self, method, path, body=None, **params):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.access}"} if self.access else {}
        if method == "get":
            return self.client.get(path, params, **headers)
        return self.client.generic(method.upper(), path, json.dumps(body or {}),
                                   content_type="application/json", **headers)

    def login(self, username="owner", password=PASSWORD):
        response = self.call("post", "/api/auth/login/", {"username": username,
                                                          "password": password})
        if response.status_code == 200:
            self.access, self.refresh = response.json()["access"], response.json()["refresh"]
        return response


def claims(token):
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_sign_in_refresh_and_sign_out(shop, tenant_b):
    app = App(shop.tenant)
    assert app.call("post", "/api/auth/login/", {"username": "owner"}).json() == {
        "password": ["This field is required."]}
    wrong = app.login(password="nope")
    assert wrong.status_code == 401 and "detail" in wrong.json()

    signed = app.login()
    assert signed.status_code == 200, signed.content
    data = signed.json()
    user = data["user"]
    assert user["username"] == "owner" and user["is_staff"] is True
    assert {"id", "first_name", "last_name", "email", "usercode", "branch", "branch_name",
            "is_active", "last_login", "permissions"} <= set(user)
    assert "access_stores" in user["permissions"] and "access_cost" in user["permissions"]
    token = claims(app.access)
    assert token["token_type"] == "access" and token["user_id"] == user["id"]
    assert 6 * 60 <= token["exp"] - clock.time() <= 7 * 60 + 5  # seven minutes

    assert app.call("get", "/api/auth/me/").json()["username"] == "owner"
    old_refresh = app.refresh
    rotated = app.call("post", "/api/auth/refresh/", {"refresh": old_refresh}).json()
    assert set(rotated) == {"access", "refresh"} and rotated["refresh"] != old_refresh
    again = app.call("post", "/api/auth/refresh/", {"refresh": old_refresh})
    assert again.status_code == 401 and again.json()["code"] == "token_not_valid"
    app.access, app.refresh = rotated["access"], rotated["refresh"]

    # A token is only good on its own client's address.
    other = App(tenant_b)
    other.access = app.access
    assert other.call("get", "/api/auth/me/").status_code == 401

    out = app.call("post", "/api/auth/logout/", {"refresh": app.refresh})
    assert out.status_code == 205
    gone = app.call("get", "/api/auth/me/")
    assert gone.status_code == 401 and gone.json()["code"] == "token_not_valid"
    assert app.call("post", "/api/auth/refresh/", {"refresh": app.refresh}).status_code == 401

    bad = App(shop.tenant)
    bad.access = "not.a.token"
    assert bad.call("get", "/api/auth/me/").status_code == 401
    assert App(shop.tenant).call("get", "/api/auth/me/").status_code == 401


def test_the_app_follows_the_feature_and_the_role(shop, make_member):
    switch(shop.tenant, on=False)
    assert App(shop.tenant).login().status_code == 403
    switch(shop.tenant)
    make_member(shop.tenant, "vera", "viewer")  # read-only: no mobile.signin
    assert App(shop.tenant).login("vera").status_code == 403
    make_member(shop.tenant, "mona", "manager")
    app = App(shop.tenant)
    assert app.login("mona").status_code == 200
    switch(shop.tenant, on=False)  # switched off while signed in
    assert app.call("get", "/Sup/api/inventory/").status_code == 403


def test_change_password(shop):
    app = App(shop.tenant)
    app.login()
    keep = app.refresh
    assert app.call("post", "/api/auth/change-password/", {
        "old_password": "nope", "new_password": "x"}).json() == {
        "old_password": ["The current password is not right."]}
    same = app.call("post", "/api/auth/change-password/", {
        "old_password": PASSWORD, "new_password": PASSWORD})
    assert same.status_code == 400 and "new_password" in same.json()
    done = app.call("post", "/api/auth/change-password/", {
        "old_password": PASSWORD, "new_password": "a-new-horse-battery-9"})
    assert done.status_code == 200 and {"access", "refresh", "detail"} <= set(done.json())
    assert app.call("post", "/api/auth/refresh/", {"refresh": keep}).status_code == 401
    assert App(shop.tenant).login(password="a-new-horse-battery-9").status_code == 200


def test_stock(shop, make_member):
    app = App(shop.tenant)
    app.login()
    listed = app.call("get", "/Sup/api/inventory/", page=1, page_size=1).json()
    assert listed["count"] == 2 and listed["has_next"] is True and listed["num_pages"] == 2
    piece = listed["results"][0]
    assert piece["mco"] == int(shop.ring_a.barcode) and piece["karat"] == 18
    assert piece["weight"] == "5.000" and piece["pieces"] == 1 and "unit_value" in piece
    assert piece["branch"] == shop.branch.code and piece["category"]
    assert app.call("get", "/Sup/api/inventory/", search=shop.ring_b.barcode).json()["count"] == 1
    assert app.call("get", "/Sup/api/inventory/", karat=21).json()["count"] == 0
    assert app.call("get", "/Sup/api/inventory/", branch=shop.other.code).json()["count"] == 0

    summary = app.call("get", "/Sup/api/inventory/summary/").json()
    assert [row["karat"] for row in summary["by_karat"]] == [1, 14, 18, 21, 22, 24]
    eighteen = next(row for row in summary["by_karat"] if row["karat"] == 18)
    assert (eighteen["weight"], eighteen["pieces"]) == ("11.000", 2)
    assert summary["total_weight"] == "11.000" and summary["total_value"] is not None

    branches = app.call("get", "/Sup/api/inventory/branches/").json()["branches"]
    assert {"br": shop.other.code, "brna": "Maadi"} in branches
    one = app.call("get", f"/Sup/api/inventory/{shop.ring_a.barcode}/")
    assert one.json()["mco"] == int(shop.ring_a.barcode)
    missing = app.call("get", "/Sup/api/inventory/999/")
    assert missing.status_code == 404 and "detail" in missing.json()

    # Without "See purchase costs" the cost fields are not sent at all (as in Gweb).
    from apps.iam.models import Role, RolePermission

    with tenant_context(shop.tenant.id):
        role = Role.objects.create(code="counter", name="Counter")
        RolePermission.objects.bulk_create([RolePermission(role=role, permission=code) for code in (
            "mobile.signin", "inventory.stock.view", "inventory.stocktake.count")])
    make_member(shop.tenant, "sami", "counter")
    counter = App(shop.tenant)
    counter.login("sami")
    row = counter.call("get", "/Sup/api/inventory/").json()["results"][0]
    assert "unit_value" not in row and "tot_cr" not in row
    assert counter.call("get", "/Sup/api/inventory/summary/").json()["total_value"] is None


def test_stocktake(shop):
    app = App(shop.tenant)
    app.login()
    lookups = app.call("get", "/Sup1/api/ghu11/lookups/").json()
    assert {"br": shop.branch.code, "brna": shop.branch.name} in lookups["branches"]
    assert {"co": 18} in lookups["karats"] and lookups["show_cost"] is True
    assert app.call("post", "/Sup1/api/ghu11/start/", {"brna": shop.branch.code}).json() == {
        "confirm": ["Confirm to start the stocktake."]}
    assert app.call("post", "/Sup1/api/ghu11/start/", {
        "brna": shop.branch.code, "karat": 9, "confirm": True}).status_code == 400

    from apps.inventory.models import Item

    with tenant_context(shop.tenant.id):  # ring B is at Maadi: not part of this count
        Item.objects.filter(pk=shop.ring_b.pk).update(branch=shop.other)
    started = app.call("post", "/Sup1/api/ghu11/start/", {
        "brna": shop.branch.code, "karat": 18, "group": None, "main_group": None,
        "confirm": True})
    assert started.status_code == 201, started.content
    head = started.json()["summary"]
    xo1 = head["xo1"]
    assert head["branch"] == shop.branch.code and head["karat"] == 18
    eighteen = next(row for row in head["karats"] if row["karat"] == 18)
    assert (eighteen["start_count"], eighteen["start_weight"], eighteen["end_count"]) == (
        1, "5.000", 0)
    busy = app.call("post", "/Sup1/api/ghu11/start/", {"brna": shop.branch.code, "confirm": True})
    assert busy.status_code == 409 and busy.json()["code"] == "stocktake_in_progress"
    assert busy.json()["xo1"] == xo1 and busy.json()["remaining"] == 1

    state = app.call("get", "/Sup1/api/ghu11/", xo1=xo1, brna=shop.branch.code, page=1,
                     page_size=25).json()
    assert len(state["remaining_items"]) == 1 and state["counted_items"] == []
    assert state["page"]["count"] == 1 and state["remaining_items"][0]["co"] == 18

    def scan(code):
        return app.call("post", "/Sup1/api/ghu11/scan/", {"xo1": xo1, "brna": shop.branch.code,
                                                          "mco": str(code)}).json()

    counted = scan(shop.ring_a.barcode)
    assert counted["success"] is True and counted["code"] == "counted"
    assert counted["item"]["counted"] is True and counted["user_count"] == 1
    assert scan(shop.ring_a.barcode)["code"] == "already_counted"
    assert scan("999999")["code"] == "not_found"
    elsewhere = scan(shop.ring_b.barcode)
    assert (elsewhere["success"], elsewhere["code"], elsewhere["branch_name"]) == (
        False, "other_branch", "Maadi")

    state = app.call("get", "/Sup1/api/ghu11/", xo1=xo1, brna=shop.branch.code).json()
    assert state["counted_count"] == 1 and len(state["counted_items"]) == 1
    end = next(row for row in state["summary"]["karats"] if row["karat"] == 18)
    assert (end["end_count"], end["diff_count"], end["end_weight"]) == (1, 0, "5.000")

    sheet = app.call("get", "/Sup1/api/ghu11/report/", xo1=xo1, brna=shop.branch.code,
                     stage="end", fmt="xlsx")
    assert sheet.status_code == 200 and sheet["Content-Type"].startswith(
        "application/vnd.openxmlformats")
    assert sheet["Content-Disposition"].startswith("attachment; filename*=UTF-8''")
    assert sheet.content[:2] == b"PK"
    assert app.call("get", "/Sup1/api/ghu11/report/", xo1=xo1, brna=shop.branch.code,
                    stage="later", fmt="pdf").status_code == 400
    assert app.call("get", "/Sup1/api/ghu11/", xo1=999, brna=shop.branch.code).status_code == 404


def test_signing_a_phone_out_from_the_web(shop):
    from conftest import login

    app = App(shop.tenant)
    app.login()
    web = login(shop.tenant, language="en")
    page = web.get("/settings/mobile-devices/").content.decode()
    assert "Android" in page and "@owner" in page
    with tenant_context(shop.tenant.id):
        device = MobileDevice.objects.get()
    web.post(f"/settings/mobile-devices/{device.pk}/sign-out/")
    with tenant_context(shop.tenant.id):
        assert MobileDevice.objects.get().revoked_at is not None
        assert MobileDevice.objects.get().revoked_at <= timezone.now()
    assert app.call("get", "/api/auth/me/").status_code == 401


def test_stocktake_pdf(shop, chromium):  # noqa: F811
    app = App(shop.tenant)
    app.login()
    xo1 = app.call("post", "/Sup1/api/ghu11/start/", {
        "brna": shop.branch.code, "confirm": True}).json()["summary"]["xo1"]
    pdf = app.call("get", "/Sup1/api/ghu11/report/", xo1=xo1, brna=shop.branch.code,
                   stage="start", fmt="pdf")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
