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


def test_console_traffic_is_live_and_switches_access(live_server, make_tenant, browser):
    """The Traffic page updates without a reload, changes period in place, and disables /
    enables a client through the confirmation dialog."""
    from apps.iam.models import User
    from apps.platform.tenants.models import Tenant, TenantStatus

    tenant = make_tenant("live")
    User.objects.create_user(email="ops@gweb.test", password=PASSWORD, display_name="Ops",
                             is_platform_staff=True)
    console = live_server.url.replace("://localhost", "://admin.localhost")
    workspace = live_server.url.replace("://localhost", "://live.localhost")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_cookies([{"name": "django_language", "value": "en", "url": console}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{console}/login/")
    page.fill("input[name=username]", "ops@gweb.test")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(f"{console}/")
    page.goto(f"{console}/traffic/?window=1h")
    row = page.locator("[data-live-part=clients] tr", has_text="Live")
    assert row.locator("td").nth(2).inner_text().strip() == "0"

    # Traffic arrives: the row updates on the next poll, no reload.
    other = browser.new_page()
    other.goto(f"{workspace}/login/")
    page.wait_for_function(
        "() => [...document.querySelectorAll('[data-live-part=clients] tr')]"
        ".some(r => r.innerText.includes('Live') && r.cells[2].innerText.trim() !== '0')",
        timeout=12000)
    assert "updated" in page.inner_text("[data-live-text]")

    # Period switch in place.
    page.click(".segmented a[href='?window=7d']")
    page.wait_for_url("**/traffic/?window=7d")
    page.wait_for_function("() => document.querySelector('[data-live]').dataset.window === '7d'")

    # Disable, after confirming; then enable again.
    row.locator("[role=switch]").click()
    page.locator("[data-confirm-dialog] button[value=confirm]").click()
    row.locator("text=Disabled").wait_for(timeout=8000)
    assert Tenant.objects.get(pk=tenant.pk).status == TenantStatus.SUSPENDED
    row.locator("[role=switch]").click()  # enabling needs no confirmation
    row.locator("text=Normal").wait_for(timeout=8000)
    assert Tenant.objects.get(pk=tenant.pk).status == TenantStatus.ACTIVE
    assert errors == []
    other.close()
    context.close()


def test_appearance_menu_switches_theme_and_accent(signed_in):
    page, base, errors = signed_in("en")
    html = page.locator("html")
    page.click("[data-appearance-toggle]")
    page.click("[data-theme-choice=dark]")
    page.click("[data-accent-choice=emerald]")
    assert html.get_attribute("data-theme") == "dark"
    assert html.get_attribute("data-accent") == "emerald"
    page.keyboard.press("Escape")
    assert page.locator("[data-appearance-panel]").is_hidden()
    page.reload()  # kept in cookies: the server renders it straight away
    assert (html.get_attribute("data-theme"), html.get_attribute("data-accent")) == (
        "dark", "emerald")
    page.click("[data-appearance-toggle]")
    page.click("[data-theme-choice=light]")
    assert html.get_attribute("data-theme") == "light"
    assert errors == []


def test_sidebar_collapses_to_an_icon_rail(signed_in):
    page, base, errors = signed_in("ar")
    sidebar = page.locator("#sidebar")
    assert sidebar.bounding_box()["width"] == 256
    page.click("[data-sidebar-collapse]")
    page.wait_for_function("() => document.getElementById('sidebar').offsetWidth === 68")
    link = page.locator("#sidebar .nav-link[aria-current=page]")
    assert link.get_attribute("title")  # the name shows on hover
    assert page.locator("#sidebar [data-sidebar-label]").first.is_hidden()
    page.reload()
    assert sidebar.bounding_box()["width"] == 68
    page.click("[data-sidebar-collapse]")
    page.wait_for_function("() => document.getElementById('sidebar').offsetWidth === 256")
    assert link.get_attribute("title") is None
    assert errors == []


def test_scroll_buttons(signed_in):
    page, base, errors = signed_in("ar", viewport=(1280, 420))
    up = page.locator("[data-scroll-to=top]")
    down = page.locator("[data-scroll-to=bottom]")

    def wait_shown(which, shown):
        page.wait_for_function(
            f"() => document.querySelector('[data-scroll-to={which}]').dataset.visible"
            f" === '{str(shown).lower()}'")

    wait_shown("bottom", True)
    assert up.get_attribute("data-visible") == "false" and up.get_attribute("tabindex") == "-1"
    down.click()
    page.wait_for_function(
        "() => scrollY + innerHeight >= document.documentElement.scrollHeight - 2")
    wait_shown("top", True)
    assert down.get_attribute("data-visible") == "false"
    up.click()
    page.wait_for_function("() => window.scrollY === 0")
    wait_shown("top", False)
    assert errors == []


def test_document_design_preview_follows_the_form(signed_in):
    page, base, errors = signed_in("en")
    page.goto(f"{base}/settings/documents/sales_invoice/")
    frame = page.frame_locator("[data-design-preview]")
    frame.locator(".cl-table").wait_for(timeout=8000)  # classic by default
    page.select_option("select[name=layout]", "modern")
    frame.locator(".md-band").wait_for(timeout=8000)
    page.fill("input[name=footer_note]", "See you soon")
    frame.locator("text=See you soon").wait_for(timeout=8000)
    assert errors == []


def test_document_designer_drag_drop_select_style_save(signed_in):
    from apps.core.tenancy import tenant_context
    from apps.platform.tenants.models import Tenant
    from apps.printing.models import DocumentDesign

    page, base, errors = signed_in("en", viewport=(1500, 950))
    page.goto(f"{base}/settings/documents/sales_invoice/designer/")
    frame = page.frame_locator("[data-frame]")
    frame.locator("[data-block]").first.wait_for(timeout=8000)
    count = page.locator("[data-layers] li[data-id]").count()
    # The toolbar selects are enhanced before the designer fills them: their lists are not empty.
    page.locator("header .ts-wrapper").first.click()
    page.locator(".ts-dropdown .option", has_text="A5").wait_for()
    page.keyboard.press("Escape")

    # Drag a "Line" block from the palette to the end of the layers...
    page.locator("[data-new=divider]").drag_to(page.locator("[data-drop-end]"))
    assert page.locator("[data-layers] li[data-id]").count() == count + 1
    # ...and a "Space" block straight onto the page.
    page.locator("[data-new=spacer]").drag_to(page.locator("[data-canvas]"),
                                               target_position={"x": 400, "y": 300})
    assert page.locator("[data-layers] li[data-id]").count() == count + 2
    frame.locator(".bk-spacer").first.wait_for(timeout=8000)

    # Click the items table in the page: the panel shows its properties; round its corners.
    frame.locator(".bk-items").first.click()
    page.locator("[data-inspector]").get_by_text("Items table").wait_for()
    page.fill("[data-field='style.radius']", "16")
    frame.locator(".bk-items[style*='border-radius:16px']").wait_for(timeout=8000)

    page.click("[data-save]")
    page.wait_for_function("() => !document.querySelector('[data-save]').disabled")
    page.locator(".toast").first.wait_for()
    tenant = Tenant.objects.get(slug="e2e")
    with tenant_context(tenant.id):
        design = DocumentDesign.objects.get()
    assert design.layout == "builder"
    assert any(b["type"] == "items" and b["style"]["radius"] == 16 for b in design.blocks)
    assert errors == []


def _printed(page):
    """Click Print in the print-table dialog; returns the HTML it sends to the print frame."""
    page.evaluate("""() => { window.__printed = null; const append = Element.prototype.append;
      document.body.append = function (...nodes) {
        nodes.forEach((n) => { if (n.tagName === "IFRAME") window.__printed = n.srcdoc; });
        return append.apply(this, nodes); }; }""")
    page.locator("[data-print-go]").click()
    page.wait_for_function("() => window.__printed")
    return page.evaluate("window.__printed")


def test_print_table_picks_columns_and_every_page(signed_in):
    from apps.core.tenancy import tenant_context
    from apps.parties.services import PartyData, create_customer
    from apps.platform.tenants.models import Tenant

    page, base, errors = signed_in("en")
    with tenant_context(Tenant.objects.get(slug="e2e").id):
        for n in range(30):  # two pages of 25
            create_customer(PartyData(name=f"Customer {n:02}", phone=f"0100000{n:04}"))

    page.goto(f"{base}/customers/")
    page.locator("main").get_by_role("button", name="Print").click()
    dialog = page.locator("[data-print-dialog]")
    dialog.wait_for()
    phone = dialog.locator("[data-print-columns] label", has_text="Phone").locator("input")
    phone.uncheck()
    dialog.locator("label", has_text="All pages").click()
    html = _printed(page)
    assert "Customer 00" in html and "Customer 29" in html  # both pages
    assert "Phone" not in html and "0100000" not in html  # the column left out
    assert errors == []
