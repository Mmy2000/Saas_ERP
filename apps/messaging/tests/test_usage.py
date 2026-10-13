"""WhatsApp usage (legacy WhatsappDashboard): the service's account in the console, and each
shop's messages this month by route."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone, translation

from apps.core.tenancy import tenant_context
from apps.iam.models import User
from apps.messaging import gateways, usage
from apps.messaging.models import OutboundMessage
from conftest import PASSWORD, login

pytestmark = pytest.mark.django_db


def _messages(tenant):
    with tenant_context(tenant.id):
        for via, status in (("api", "sent"), ("api", "sent"), ("own", "sent"),
                            ("link", "shared"), ("api", "failed")):
            OutboundMessage.objects.create(channel="whatsapp", kind="document", via=via,
                                           status=status, recipient="201001234567")
        old = OutboundMessage.objects.create(channel="whatsapp", kind="document", via="api",
                                             status="sent", recipient="201001234567")
        start, _end = usage.month_bounds()
        OutboundMessage.objects.filter(pk=old.pk).update(created_at=start - timedelta(days=3))
        OutboundMessage.objects.create(channel="email", kind="document", status="sent",
                                       recipient="a@b.test")


def test_counts_by_route_and_month(tenant_a):
    _messages(tenant_a)
    with tenant_context(tenant_a.id):
        now = usage.usage(*usage.month_bounds())
        before = usage.usage(*usage.month_bounds(back=1))
    assert now.sent == {"api": 2, "own": 1, "link": 1} and now.failed == 1 and now.total == 4
    assert before.total == 1
    start, end = usage.month_bounds(timezone.localdate().replace(month=1, day=15), back=1)
    assert (start.month, start.year + 1, end.month) == (12, timezone.localdate().year, 1)


def test_hosted_account(settings, monkeypatch):
    settings.WHATSAPP_CONSOLE = False
    settings.WHATSAPP_GATEWAY_URL, settings.WHATSAPP_GATEWAY_KEY = "https://wa.test", "k"
    answers = {"/accounts/profile/": (200, {"messages_sent_count": 750,
                                            "messages_quota": 1000}),
               "/accounts/messages/": (200, [{"phone": "2010", "status": "sent"}])}
    monkeypatch.setattr(gateways, "_call", lambda method, url, **kw: answers[
        url.removeprefix("https://wa.test")])
    found = gateways.hosted_usage()
    assert found.ok and found.data["sent"] == 750 and found.data["quota"] == 1000
    assert found.data["messages"] == [{"phone": "2010", "status": "sent"}]
    monkeypatch.setattr(gateways, "_call", lambda method, url, **kw: (401, {}))
    with translation.override("en"):
        refused = gateways.hosted_usage()
    assert not refused.ok and "key" in refused.error


def test_console_and_shop_pages(settings, tenant_a):
    settings.WHATSAPP_CONSOLE = True
    _messages(tenant_a)
    User.objects.create_user(email="ops@gweb.test", password=PASSWORD, display_name="Ops",
                             is_platform_staff=True)
    console = Client(HTTP_HOST="admin.localhost")
    console.cookies["django_language"] = "en"
    console.post("/login/", {"username": "ops@gweb.test", "password": PASSWORD})
    page = console.get("/whatsapp/").content.decode()
    assert "Quota" in page and tenant_a.name in page and "data-account-sent" in page
    shop = login(tenant_a, language="en").get("/settings/messages/").content.decode()
    assert "WhatsApp this month" in shop and "data-whatsapp-usage" in shop
