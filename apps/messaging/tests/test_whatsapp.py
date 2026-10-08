"""WhatsApp through a gateway (legacy whatsapp_gateway and the hosted WhatsApp API): linking the
shop's number by QR, sending documents through either gateway, failures and the switches."""

import time as clock

import pytest
from django.utils import translation

from apps.core.tenancy import tenant_context
from apps.messaging import gateways
from apps.messaging.models import MessageStatus, OutboundMessage, Via, WhatsAppLink
from apps.platform.jobs.models import Job
from apps.platform.jobs.worker import run_pending
from apps.platform.tenants import features
from apps.platform.tenants.models import TenantFeature
from apps.printing.tests.test_documents import owner, sale  # noqa: F401  (fixtures)

pytestmark = pytest.mark.django_db
LINK = "/api/v1/messages/whatsapp/link/"


def switch(tenant, **keys):
    for key, on in keys.items():
        TenantFeature.objects.update_or_create(tenant=tenant, key=key, defaults={"enabled": on})
    features.forget(tenant.pk)
    # The worker writes in the shop's language: an English shop, for the assertions.
    from apps.org.models import TenantProfile

    with tenant_context(tenant.id):
        TenantProfile.objects.update(locale="en")


def _send(client, **body):
    return client.post("/api/v1/messages/send/", body, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY=f"k-{clock.perf_counter_ns()}")


def _last(tenant):
    with tenant_context(tenant.id):
        return OutboundMessage.objects.order_by("-id").first()


@pytest.fixture(autouse=True)
def _english():
    with translation.override("en"):
        yield


@pytest.fixture(autouse=True)
def _outbox(monkeypatch):
    gateways.OUTBOX.clear()
    gateways._CONSOLE_SESSIONS.clear()
    # The PDF itself is covered by the e-mail tests; a stand-in keeps these quick.
    monkeypatch.setattr("apps.messaging.jobs.message_pdf",
                        lambda message: (b"%PDF-1.7", f"{message.doc_label}.pdf"))


def link(client):
    waiting = client.post(LINK, HTTP_IDEMPOTENCY_KEY="l1").json()  # started: the QR follows
    assert waiting["status"] == "qr" and waiting["qr"].startswith("data:image/")
    linked = client.get(LINK).json()  # the console "scans" it
    assert linked["status"] == "connected" and linked["phone"] == "201000000000"


def test_phone_numbers():
    assert gateways.phone_digits("01001234567") == "201001234567"
    assert gateways.phone_digits("+20 100 123 4567") == "201001234567"
    assert gateways.phone_digits("00971501234567") == "971501234567"
    assert gateways.phone_digits("123") == ""


def test_link_the_shops_number_and_send_from_it(owner, tenant_a, sale):  # noqa: F811
    draft = owner.get(f"/api/v1/messages/draft/?doc_type=sales_invoice&pk={sale.pk}").json()
    assert draft["whatsapp_routes"] == ["link"]  # both gateways are off unless switched on
    assert owner.get("/settings/whatsapp/").status_code == 403
    assert owner.get(LINK).status_code == 403

    switch(tenant_a, whatsapp_own=True)
    page = owner.get("/settings/whatsapp/").content.decode()
    assert "Link a number" in page
    link(owner)
    draft = owner.get(f"/api/v1/messages/draft/?doc_type=sales_invoice&pk={sale.pk}").json()
    assert draft["whatsapp_routes"] == ["own", "link"]

    test = owner.post(f"{LINK}test/", {"to": "01001234567"}, content_type="application/json")
    assert test.json()["status"] == "sent"
    assert gateways.OUTBOX[-1]["phone"] == "201001234567" and "test" in gateways.OUTBOX[-1]["text"]

    sent = _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="whatsapp", via="own",
                 to="01001234567", message="Your invoice, thank you.")
    assert sent.status_code == 201, sent.content
    message = _last(tenant_a)
    assert (message.status, message.via, message.recipient) == (MessageStatus.QUEUED, Via.OWN,
                                                               "201001234567")
    assert Job.objects.filter(name="messaging.send_whatsapp").count() == 1
    run_pending()
    message = _last(tenant_a)
    assert message.status == MessageStatus.SENT and message.sent_at
    out = gateways.OUTBOX[-1]
    assert out["via"] == "own" and out["text"] == "Your invoice, thank you."
    assert sale.number in out["filename"]
    assert "Shop&#x27;s number" in owner.get("/settings/messages/").content.decode() or \
        "Shop's number" in owner.get("/settings/messages/").content.decode()

    bad = _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="whatsapp", via="own",
                to="12")
    assert bad.status_code == 400 and "to" in bad.json()["error"]["fields"]
    assert owner.delete(LINK).json()["status"] == "not_started"
    with tenant_context(tenant_a.id):
        assert not WhatsAppLink.objects.exists()


def test_unlinked_number_fails_in_words(owner, tenant_a, sale):  # noqa: F811
    switch(tenant_a, whatsapp_own=True)
    link(owner)
    _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="whatsapp", via="own",
          to="201001234567")
    with tenant_context(tenant_a.id):
        WhatsAppLink.objects.update(status="disconnected")  # the phone was logged out
    run_pending()
    message = _last(tenant_a)
    assert message.status == MessageStatus.FAILED
    assert message.error.startswith("The shop's WhatsApp number is not linked")  # no "JobFailed"
    assert "Send again" in owner.get("/settings/messages/?show=failed").content.decode()

    with tenant_context(tenant_a.id):
        WhatsAppLink.objects.update(status="connected")
    again = owner.post(f"/api/v1/messages/{message.pk}/again/", HTTP_IDEMPOTENCY_KEY="a1")
    assert again.status_code == 200
    run_pending()
    assert _last(tenant_a).status == MessageStatus.SENT


def test_the_hosted_service(owner, tenant_a, sale, make_member):  # noqa: F811
    assert _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="whatsapp", via="api",
                 to="201001234567").status_code == 400  # switched off for this client
    switch(tenant_a, whatsapp_api=True)
    draft = owner.get(f"/api/v1/messages/draft/?doc_type=sales_invoice&pk={sale.pk}").json()
    assert draft["whatsapp_routes"] == ["api", "link"]
    assert _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="whatsapp", via="api",
                 to="201001234567").status_code == 201
    run_pending()
    out = gateways.OUTBOX[-1]
    assert out["via"] == "api" and out["order_id"] == sale.number and not out["statement"]
    assert out["amount"] not in ("", "0")
    assert _last(tenant_a).status == MessageStatus.SENT
    # The own-number settings page is not part of the hosted service.
    assert owner.get("/settings/whatsapp/").status_code == 403


def test_a_gateway_that_is_down(owner, tenant_a, sale, settings, monkeypatch):  # noqa: F811
    settings.WHATSAPP_CONSOLE = False
    settings.WHATSAPP_GATEWAY_URL, settings.WHATSAPP_GATEWAY_KEY = "http://127.0.0.1:9", "k"
    switch(tenant_a, whatsapp_api=True)
    _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="whatsapp", via="api",
          to="201001234567")
    run_pending()
    message = _last(tenant_a)
    assert message.status == MessageStatus.QUEUED  # tried again later
    assert "not reachable" in message.error
