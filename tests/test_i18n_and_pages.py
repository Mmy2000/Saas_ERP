"""Bilingual UI (Arabic default, English on request) and a render check of every page."""

import polib
import pytest

from apps.core.tenancy import tenant_context
from apps.parties.services import PartyData, create_customer, create_supplier
from conftest import login
from ops.i18n.messages import MO_PATH, PO_PATH, problems

PAGES = ["/", "/branches/", "/catalog/karats/", "/catalog/categories/", "/pricing/gold/",
         "/pricing/fx/", "/customers/", "/customers/new/", "/suppliers/", "/suppliers/new/",
         "/branches/new/", "/settings/users/", "/settings/users/new/", "/settings/roles/",
         "/settings/roles/new/", "/accounting/accounts/", "/accounting/journal/",
         "/accounting/journal/new/", "/accounting/trial-balance/", "/stock/", "/purchasing/",
         "/purchasing/new/", "/sales/", "/sales/new/", "/settlements/",
         "/settlements/new/", "/treasury/", "/treasury/movements/", "/treasury/movements/new/",
         "/treasury/box/new/", "/treasury/bank/new/", "/treasury/terminal/new/", "/expenses/",
         "/expenses/new/", "/expenses/categories/", "/stock/transfers/", "/stock/transfers/new/",
         "/stock/stocktakes/", "/stock/stocktakes/new/", "/stock/labels/", "/settings/labels/",
         "/settings/labels/new/", "/sales/reservations/", "/sales/reservations/?state=all",
         "/sales/reservations/new/", "/reports/", "/reports/daily_summary/",
         "/reports/gold_balances/", "/reports/sales/", "/reports/expenses/", "/purchasing/scrap/",
         "/purchasing/scrap/?tab=sales", "/purchasing/scrap/buy/", "/purchasing/scrap/sell/",
         "/purchasing/returns/", "/purchasing/returns/new/", "/sales/wholesale/",
         "/sales/wholesale/new/", "/trade-accounts/", "/trade-accounts/new/",
         "/settlements/new/?side=trade_account", "/workshops/", "/workshops/new/",
         "/manufacturing/", "/manufacturing/?state=all", "/manufacturing/new/",
         "/settlements/new/?side=workshop", "/repairs/", "/repairs/?state=all",
         "/repairs/new/", "/hr/", "/hr/?status=all", "/hr/new/", "/hr/payroll/",
         "/hr/payroll/new/", "/hr/commissions/", "/accounting/periods/",
         "/production/", "/production/new/", "/accounting/profit-and-loss/",
         "/accounting/balance-sheet/", "/treasury/cheques/", "/treasury/cheques/new/",
         "/treasury/counts/", "/treasury/counts/new/", "/settings/activity/"]


def test_every_string_is_translated_to_arabic():
    assert problems() == [], "Run: python ops/i18n/messages.py extract, translate, compile"


def test_compiled_catalogue_matches_source():
    source = {e.msgid: e.msgstr for e in polib.pofile(str(PO_PATH)) if not e.msgid_plural}
    compiled = {e.msgid: e.msgstr for e in polib.mofile(str(MO_PATH)) if not e.msgid_plural}
    assert compiled == source, "Run: python ops/i18n/messages.py compile"


@pytest.mark.django_db
class TestLanguage:
    def test_arabic_is_the_default(self, tenant_a):
        html = login(tenant_a).get("/").content.decode()
        assert 'lang="ar" dir="rtl"' in html
        assert "لوحة التحكم" in html

    def test_english_on_request(self, tenant_a):
        html = login(tenant_a, language="en").get("/").content.decode()
        assert 'lang="en" dir="ltr"' in html
        assert "Dashboard" in html

    def test_tenant_default_language(self, make_tenant):
        tenant = make_tenant("london", locale="en", country="GB", functional_currency="GBP")
        html = login(tenant).get("/").content.decode()
        assert 'lang="en"' in html
        with tenant_context(tenant.id):
            from apps.org.models import Branch

            assert Branch.objects.get(code=1).name == "Head office"

    def test_switching_language_sets_the_cookie(self, tenant_a):
        client = login(tenant_a)
        response = client.post("/i18n/setlang/", {"language": "en", "next": "/"})
        assert response.status_code == 302
        assert client.cookies["django_language"].value == "en"

    def test_login_page_in_both_languages(self, tenant_a):
        from django.test import Client

        client = Client(HTTP_HOST="alpha.localhost")
        assert "تسجيل الدخول" in client.get("/login/").content.decode()
        client.cookies["django_language"] = "en"
        assert "Sign in to your workspace" in client.get("/login/").content.decode()

    def test_numbers_use_western_digits_and_dot_decimals(self, tenant_a):
        from apps.pricing.services import (
            KaratPriceInput,
            PublishPriceBoardCommand,
            publish_price_board,
        )

        with tenant_context(tenant_a.id):
            from apps.catalog.models import Karat

            publish_price_board(PublishPriceBoardCommand(
                reference=KaratPriceInput(Karat.objects.get(code=21).pk, "4000")))
        html = login(tenant_a).get("/pricing/gold/").content.decode()
        assert "4,570.97" in html  # 24K derived from 21K @ 4000


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["ar", "en"])
def test_every_page_renders(tenant_a, language):
    with tenant_context(tenant_a.id):
        customer = create_customer(PartyData(name="Mona", phone="01012345678"))
        supplier = create_supplier(PartyData(name="Factory"))
    client = login(tenant_a, language=language)
    with tenant_context(tenant_a.id):
        from apps.iam.models import Membership, Role
        from apps.org.models import Branch

        extra = [f"/settings/users/{Membership.objects.get(username='owner').pk}/",
                 f"/settings/roles/{Role.objects.get(code='viewer').pk}/",
                 f"/branches/{Branch.objects.get(code=1).pk}/"]
    for path in [*PAGES, *extra, f"/customers/{customer.pk}/", f"/suppliers/{supplier.pk}/"]:
        response = client.get(path)
        assert response.status_code == 200, path


def test_template_strings_with_percent_signs_translate():
    """{% translate %} and {% blocktranslate %} look strings up with "%" doubled."""
    from django.template import Context, Template
    from django.utils import translation

    with translation.override("ar"):
        rendered = Template('{% load i18n %}{% translate "Discount %" %}').render(Context())
    assert rendered == "الخصم %"
