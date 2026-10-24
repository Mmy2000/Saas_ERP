"""Too many wrong passwords lock that account (and, at a higher count, that address) for a
while, on every way in: workspace, console, mobile app (apps.iam.throttle)."""

import pytest
from django.test import Client

from apps.iam import throttle
from apps.iam.models import User
from conftest import PASSWORD

pytestmark = pytest.mark.django_db
WRONG = "not-the-password"


def _client(host="alpha.localhost", address="198.51.100.7"):
    client = Client(HTTP_HOST=host, REMOTE_ADDR=address)
    client.cookies["django_language"] = "en"
    return client


def _sign_in(client, username, password):
    return client.post("/login/", {"username": username, "password": password})


def test_five_wrong_passwords_lock_the_account_for_a_while(tenant_a, make_member, monkeypatch):
    make_member(tenant_a, "nour")
    client = _client()
    for _ in range(throttle.ACCOUNT_LIMIT):
        assert _sign_in(client, "owner", WRONG).status_code == 200
    # Locked: even the right password is refused, and the page says why.
    locked = _sign_in(client, "owner", PASSWORD)
    assert locked.status_code == 200 and b"Too many wrong passwords" in locked.content
    # Only that account: another member still signs in from the same address.
    assert _sign_in(client, "nour", PASSWORD).status_code == 302

    later = throttle.time.time() + throttle.WINDOW_SECONDS + 1
    monkeypatch.setattr(throttle.time, "time", lambda: later)
    assert _sign_in(_client(), "owner", PASSWORD).status_code == 302


def test_the_right_password_starts_the_count_again(tenant_a):
    client = _client()
    for _ in range(throttle.ACCOUNT_LIMIT - 1):
        _sign_in(client, "owner", WRONG)
    assert _sign_in(client, "owner", PASSWORD).status_code == 302
    for _ in range(throttle.ACCOUNT_LIMIT - 1):
        _sign_in(_client(), "owner", WRONG)
    assert _sign_in(_client(), "owner", PASSWORD).status_code == 302


def test_one_address_trying_many_accounts_is_stopped(tenant_a):
    client = _client(address="203.0.113.9")
    for number in range(throttle.ADDRESS_LIMIT):
        _sign_in(client, f"guess{number}", WRONG)
    assert b"Too many wrong passwords" in _sign_in(client, "owner", PASSWORD).content
    assert _sign_in(_client(address="192.0.2.1"), "owner", PASSWORD).status_code == 302


def test_the_console_and_the_mobile_app_are_locked_too(tenant_a):
    User.objects.create_user(email="ops@gweb.test", password=PASSWORD, is_platform_staff=True)
    console = _client(host="admin.localhost")
    for _ in range(throttle.ACCOUNT_LIMIT):
        _sign_in(console, "ops@gweb.test", WRONG)
    assert b"Too many wrong passwords" in _sign_in(console, "ops@gweb.test", PASSWORD).content

    app = _client(address="198.51.100.20")
    for _ in range(throttle.ACCOUNT_LIMIT):
        assert app.post("/api/auth/login/", {"username": "owner", "password": WRONG},
                        content_type="application/json").status_code == 401
    refused = app.post("/api/auth/login/", {"username": "owner", "password": PASSWORD},
                       content_type="application/json")
    assert refused.status_code == 429 and int(refused["Retry-After"]) > 0
