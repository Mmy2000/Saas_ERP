"""Browser checks (§23.2 E2E). Opt-in: `pytest --e2e` (needs `playwright install chromium`).

They catch what server-side tests cannot: JS errors, un-enhanced selects, and layouts that
overflow on phones (all three happened while building the UI).
"""

import os

import pytest

from conftest import PASSWORD

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

PAGES = ["/", "/branches/", "/catalog/karats/", "/catalog/categories/", "/pricing/gold/",
         "/pricing/fx/", "/customers/", "/customers/new/", "/suppliers/", "/suppliers/new/",
         "/branches/new/", "/settings/users/", "/settings/users/new/", "/settings/roles/",
         "/settings/roles/new/", "/accounting/accounts/", "/accounting/journal/",
         "/accounting/journal/new/", "/accounting/trial-balance/", "/stock/", "/purchasing/",
         "/purchasing/new/", "/sales/", "/sales/new/", "/settlements/",
         "/settlements/new/", "/sales/wholesale/new/", "/trade-accounts/", "/workshops/",
         "/manufacturing/", "/manufacturing/new/", "/purchasing/returns/new/", "/repairs/",
         "/repairs/new/"]


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    # Playwright's sync API keeps an event loop running in this thread; Django's test-DB
    # setup/teardown would otherwise refuse to run next to it. Test-only.
    previous = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    try:
        with playwright.sync_playwright() as p:
            instance = p.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
            yield instance
            instance.close()
    finally:
        if previous is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = previous


@pytest.fixture
def signed_in(live_server, make_tenant, browser):
    """Returns open(language, viewport) → (page, base_url, js_errors) signed in as the owner."""
    make_tenant("e2e")
    base = live_server.url.replace("://localhost", "://e2e.localhost")
    contexts = []

    def _open(language="ar", viewport=(1280, 800)):
        context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        contexts.append(context)
        context.add_cookies([{"name": "django_language", "value": language, "url": base}])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))
        page.goto(f"{base}/login/")
        page.fill("#id_username", "owner")
        page.fill("#id_password", PASSWORD)
        page.press("#id_password", "Enter")
        page.wait_for_url(f"{base}/")
        return page, base, errors

    yield _open
    for context in contexts:
        context.close()


@pytest.mark.parametrize("language", ["ar", "en"])
def test_every_select_is_searchable(signed_in, language):
    page, base, errors = signed_in(language)
    page.goto(f"{base}/suppliers/new/")
    assert page.locator("select").count() == page.locator(".ts-wrapper").count() > 0

    page.click("#f-karat-ts-control")
    page.wait_for_selector(".ts-dropdown .dropdown-input:focus")
    page.keyboard.type("18")
    page.keyboard.press("Enter")
    chosen = page.evaluate("() => document.querySelector('#f-karat').selectedOptions[0].value")
    assert chosen, "typing a karat and pressing Enter should select it"
    assert errors == []


@pytest.mark.parametrize("language", ["ar", "en"])
def test_pages_fit_a_phone_and_have_no_js_errors(signed_in, language):
    page, base, errors = signed_in(language, viewport=(390, 844))
    for path in PAGES:
        page.goto(base + path)
        scroll, client = page.evaluate(
            "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert scroll <= client, f"{path} overflows horizontally ({scroll} > {client})"
    assert errors == []


def test_rtl_document_and_sidebar_side(signed_in):
    page, base, _ = signed_in("ar")
    assert page.evaluate("() => document.documentElement.dir") == "rtl"
    box = page.locator("#sidebar").bounding_box()
    assert box["x"] > 1000, "in Arabic the sidebar sits on the right"


@pytest.mark.parametrize("language", ["ar", "en"])
def test_command_palette_opens_a_screen(signed_in, language):
    page, base, errors = signed_in(language)
    page.keyboard.press("Control+k")
    page.wait_for_selector("[data-cmdk][open]")
    page.keyboard.type("مورد" if language == "ar" else "suppl")
    visible = page.locator("[data-cmdk-item]:not([hidden])")
    assert visible.count() >= 1
    page.keyboard.press("Enter")
    page.wait_for_url(f"{base}/suppliers/")
    assert errors == []
