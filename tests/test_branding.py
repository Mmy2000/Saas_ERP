"""Branding (platform settings, client logos) and uploaded files kept apart per client."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.core.tenancy import tenant_context
from apps.iam.models import User
from apps.platform.tenants.models import PlatformEvent, PlatformLink, PlatformSettings
from conftest import PASSWORD, login

pytestmark = pytest.mark.django_db
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def png(name="logo.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


@pytest.fixture
def console(db):
    User.objects.create_user(email="ops@gweb.test", password=PASSWORD, display_name="Ops",
                             is_platform_staff=True)
    client = Client(HTTP_HOST="admin.localhost")
    client.cookies["django_language"] = "en"
    assert client.post("/login/", {"username": "ops@gweb.test", "password": PASSWORD}
                       ).status_code == 302
    return client


def _profile(tenant):
    from apps.org.models import TenantProfile

    with tenant_context(tenant.id):
        return TenantProfile.objects.get()


def test_client_uploads_its_logo_into_its_own_folder(tenant_a, media):
    client = login(tenant_a)
    client.cookies["django_language"] = "en"
    page = client.get("/settings/company/")
    assert page.status_code == 200 and "Company" in page.content.decode()
    response = client.post("/settings/company/", {
        "display_name": "Alpha Gold", "accent": "blue", "logo": png()})
    assert response.status_code == 302
    profile = _profile(tenant_a)
    assert profile.logo.name.startswith(f"tenants/{tenant_a.id}/branding/")
    assert (profile.display_name, profile.accent) == ("Alpha Gold", "blue")
    first = profile.logo.name
    assert (media / first).exists()

    html = client.get("/").content.decode()
    assert f"/media/{first}" in html  # sidebar and browser tab

    # Replacing deletes the old file; removing clears it.
    client.post("/settings/company/", {"display_name": "Alpha Gold", "accent": "blue",
                                       "logo": png("new.png")})
    assert not (media / first).exists()
    client.post("/settings/company/", {"display_name": "Alpha Gold", "accent": "blue",
                                       "logo-clear": "on"})
    assert not _profile(tenant_a).logo


def test_only_images_are_accepted(tenant_a):
    client = login(tenant_a)
    client.cookies["django_language"] = "en"
    svg = SimpleUploadedFile("logo.svg", b"<svg onload='alert(1)'/>", content_type="image/svg+xml")
    page = client.post("/settings/company/", {"display_name": "A", "accent": "gold", "logo": svg})
    assert page.status_code == 200 and "Upload a PNG, JPEG" in page.content.decode()
    fake = SimpleUploadedFile("logo.png", b"not really a png", content_type="image/png")
    page = client.post("/settings/company/", {"display_name": "A", "accent": "gold", "logo": fake})
    assert "Upload a PNG, JPEG" in page.content.decode()
    assert not _profile(tenant_a).logo


def test_company_page_needs_the_permission(tenant_a, make_member):
    make_member(tenant_a, "clerk", "viewer")
    assert login(tenant_a, username="clerk").get("/settings/company/").status_code == 403


def test_files_never_cross_between_clients(tenant_a, tenant_b, media, console):
    client_a = login(tenant_a)
    client_a.post("/settings/company/", {"display_name": "A", "accent": "gold", "logo": png()})
    logo = _profile(tenant_a).logo.name
    private = media / f"tenants/{tenant_a.id}/documents/contract.png"
    private.parent.mkdir(parents=True)
    private.write_bytes(PNG)
    private_url = f"/media/tenants/{tenant_a.id}/documents/contract.png"

    # The logo is public on A's own host (the sign-in page shows it)...
    anonymous_a = Client(HTTP_HOST="alpha.localhost")
    response = anonymous_a.get(f"/media/{logo}")
    assert response.status_code == 200 and response["X-Content-Type-Options"] == "nosniff"
    # ...other files need a member of A...
    assert anonymous_a.get(private_url).status_code == 404
    assert client_a.get(private_url).status_code == 200
    # ...and nothing of A's is reachable from B, signed in or not.
    client_b = login(tenant_b)
    assert client_b.get(f"/media/{logo}").status_code == 404
    assert client_b.get(private_url).status_code == 404
    # Platform staff can open any client's files; anonymous visitors of the console cannot.
    assert console.get(private_url).status_code == 200
    assert Client(HTTP_HOST="admin.localhost").get(private_url).status_code == 404
    # No escaping the media folder.
    for path in ("/media/../manage.py", "/media/tenants/../../manage.py",
                 f"/media/tenants/{tenant_a.id}/branding/.hidden"):
        assert client_a.get(path).status_code == 404


def test_platform_settings(console, tenant_a, media):
    page = console.get("/settings/")
    assert page.status_code == 200 and "Platform settings" in page.content.decode()
    response = console.post("/settings/", {
        "brand_name": "Mahmoud Soft", "tagline": "ERP by Mahmoud Soft",
        "footer_text": "© 2026 Mahmoud Soft", "logo": png(),
        "links-TOTAL_FORMS": "2", "links-INITIAL_FORMS": "0",
        "links-0-label": "Support", "links-0-url": "https://example.com/support",
        "links-0-position": "1",
        "links-1-label": "", "links-1-url": "", "links-1-position": "0",
    })
    assert response.status_code == 302, response.content.decode()[:500]
    row = PlatformSettings.objects.get()
    assert row.brand_name == "Mahmoud Soft" and row.logo.name.startswith("platform/branding/")
    assert list(PlatformLink.objects.values_list("label", "url")) == [
        ("Support", "https://example.com/support")]
    assert PlatformEvent.objects.filter(action="platform.settings").exists()

    # Every workspace shows it: the line under the client's name, the footer, the logo.
    html = login(tenant_a).get("/").content.decode()
    assert "ERP by Mahmoud Soft" in html and "© 2026 Mahmoud Soft" in html
    assert "https://example.com/support" in html and f"/media/{row.logo.name}" in html
    assert "Gweb" not in html
    # The platform logo is public on any host (the client's sign-in page shows it).
    assert Client(HTTP_HOST="alpha.localhost").get(f"/media/{row.logo.name}").status_code == 200
    login_page = Client(HTTP_HOST="alpha.localhost").get("/login/").content.decode()
    assert "Mahmoud Soft" in login_page

    # The console shows a client's logo field and can set it too.
    console.post(f"/clients/{tenant_a.pk}/profile/", {
        "display_name": "Alpha", "locale": "en", "country": "EG", "timezone": "Africa/Cairo",
        "accent": "gold", "logo": png()})
    assert _profile(tenant_a).logo.name.startswith(f"tenants/{tenant_a.id}/branding/")
    assert console.get(f"/clients/{tenant_a.pk}/").status_code == 200
