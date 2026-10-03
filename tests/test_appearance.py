"""Theme and accent colour: cookies chosen in the appearance menu, the workspace's colour."""

import pytest
from django.test import Client

from apps.core.tenancy import tenant_context
from conftest import login

pytestmark = pytest.mark.django_db


def _html_tag(response) -> str:
    html = response.content.decode()
    return html[html.index("<html"):html.index(">", html.index("<html"))]


def test_defaults_follow_the_system_and_the_workspace(tenant_a):
    tag = _html_tag(login(tenant_a).get("/"))
    assert 'data-theme="light"' in tag and 'data-theme-pref="system"' in tag
    assert 'data-accent="gold"' in tag and "data-appearance" in login(tenant_a).get(
        "/").content.decode()


def test_the_users_choice_wins(tenant_a):
    client = login(tenant_a)
    client.cookies["gweb_theme"] = "dark"
    client.cookies["gweb_accent"] = "violet"
    tag = _html_tag(client.get("/"))
    assert 'data-theme="dark"' in tag and 'data-theme-pref="dark"' in tag
    assert 'data-accent="violet"' in tag and 'data-accent-default="gold"' in tag


def test_workspace_colour_and_bad_cookies(tenant_a):
    from apps.org.models import TenantProfile

    with tenant_context(tenant_a.id):
        TenantProfile.objects.update(accent="teal")
    client = login(tenant_a)
    client.cookies["gweb_theme"] = "neon"
    client.cookies["gweb_accent"] = "<script>"
    tag = _html_tag(client.get("/"))
    assert 'data-theme-pref="system"' in tag and 'data-accent="teal"' in tag
    # The login page (no member yet) has the workspace colour too.
    assert 'data-accent="teal"' in _html_tag(Client(HTTP_HOST="alpha.localhost").get("/login/"))


def test_label_sheets_are_always_light(tenant_a):
    client = login(tenant_a)
    client.cookies["gweb_theme"] = "dark"
    response = client.get("/stock/labels/")
    assert response.status_code == 200
    assert 'data-theme="light"' in _html_tag(response)
    assert 'data-theme-pref="light"' in _html_tag(response)


def test_collapsed_sidebar_is_remembered(tenant_a):
    client = login(tenant_a)
    assert 'data-sidebar="expanded"' in _html_tag(client.get("/"))
    client.cookies["gweb_sidebar"] = "collapsed"
    response = client.get("/")
    assert 'data-sidebar="collapsed"' in _html_tag(response)
    assert "data-sidebar-collapse" in response.content.decode()
