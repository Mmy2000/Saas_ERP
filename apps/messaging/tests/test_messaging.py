"""Sending documents and statements by e-mail and WhatsApp, the log, and the daily reminders."""

import time as clock
from urllib.parse import unquote

import pytest
from django.core import mail, signing
from django.test import Client

from apps.core.tenancy import tenant_context
from apps.messaging.models import DigestRecipient, MessageStatus, OutboundMessage
from apps.platform.jobs.models import Job, JobStatus
from apps.platform.jobs.worker import run_pending
from apps.printing.tests.test_documents import owner, sale  # noqa: F401  (fixtures)
from apps.printing.tests.test_pdf import chromium  # noqa: F401  (fixture)
from conftest import login

pytestmark = pytest.mark.django_db
SEND = "/api/v1/messages/send/"


def _send(client, **body):
    return client.post(SEND, body, content_type="application/json",
                       HTTP_IDEMPOTENCY_KEY=f"k-{clock.perf_counter_ns()}")


def _message(tenant):
    with tenant_context(tenant.id):
        return OutboundMessage.objects.order_by("-id").first()


def test_email_an_invoice(chromium, owner, tenant_a, sale):  # noqa: F811
    draft = owner.get(f"/api/v1/messages/draft/?doc_type=sales_invoice&pk={sale.pk}").json()
    assert sale.number in draft["label"] and "attached" in draft["email_text"]

    response = _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="email",
                     to="buyer@example.com", message="Your invoice, thank you.")
    assert response.status_code == 201, response.content
    message = _message(tenant_a)
    assert (message.status, message.recipient, message.doc_model) == (
        MessageStatus.QUEUED, "buyer@example.com", "sales.SalesInvoice")
    assert Job.objects.get().name == "messaging.send_email"
    assert mail.outbox == []  # nothing is sent inside the request

    assert run_pending() == 1
    sent = mail.outbox[0]
    assert sent.to == ["buyer@example.com"] and sale.number in sent.subject
    assert sent.body == "Your invoice, thank you." and sent.reply_to == ["owner@alpha.test"]
    filename, content, kind = sent.attachments[0]
    assert kind == "application/pdf" and content.startswith(b"%PDF-") and sale.number in filename
    message = _message(tenant_a)
    assert message.status == MessageStatus.SENT and message.sent_at

    page = owner.get("/settings/messages/").content.decode()
    assert "buyer@example.com" in page and f"/sales/{sale.pk}/" in page


def test_a_bad_address_and_who_may_send(owner, tenant_a, tenant_b, make_member, sale):  # noqa: F811
    response = _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="email", to="nope")
    assert response.status_code == 400 and "to" in response.json()["error"]["fields"]

    make_member(tenant_a, "viewer", "viewer")
    viewer = login(tenant_a, username="viewer")
    assert _send(viewer, doc_type="sales_invoice", pk=sale.pk, channel="email",
                 to="a@b.co").status_code == 403
    assert viewer.get("/settings/messages/").status_code == 200  # may see the log
    assert "data-send-open" not in viewer.get(f"/sales/{sale.pk}/").content.decode()
    assert "data-send-open" in owner.get(f"/sales/{sale.pk}/").content.decode()

    other = login(tenant_b)
    assert _send(other, doc_type="sales_invoice", pk=sale.pk, channel="email",
                 to="a@b.co").status_code == 404
    assert _send(owner, doc_type="nope", pk=1, channel="email", to="a@b.co").status_code == 404


def test_a_failed_email_is_shown_and_can_be_sent_again(owner, tenant_a, sale,  # noqa: F811
                                                         monkeypatch):
    from django.core.mail import EmailMultiAlternatives

    def down(self, *args, **kwargs):
        raise ConnectionRefusedError("SMTP server refused the connection")

    monkeypatch.setattr("apps.messaging.jobs.message_pdf", lambda message: (b"%PDF-1", "x.pdf"))
    monkeypatch.setattr(EmailMultiAlternatives, "send", down)
    _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="email", to="b@example.com")
    job = Job.objects.get()
    for _attempt in range(job.max_attempts):
        Job.objects.filter(pk=job.pk).update(run_at=job.created_at)
        run_pending()
        message = _message(tenant_a)
        assert "refused" in message.error
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED and message.status == MessageStatus.FAILED
    assert "Send again" in owner.get("/settings/messages/?show=failed").content.decode()

    monkeypatch.undo()
    monkeypatch.setattr("apps.messaging.jobs.message_pdf", lambda message: (b"%PDF-1", "x.pdf"))
    again = owner.post(f"/api/v1/messages/{message.pk}/again/", HTTP_IDEMPOTENCY_KEY="again-1")
    assert again.status_code == 200
    run_pending()
    assert _message(tenant_a).status == MessageStatus.SENT and len(mail.outbox) == 1


def test_share_on_whatsapp_with_a_private_link(chromium, owner, tenant_a, tenant_b,  # noqa: F811
                                               sale, monkeypatch):  # noqa: F811
    response = _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="whatsapp",
                     to="+20 100 123 4567", message="Your invoice")
    assert response.status_code == 201
    wa_url = response.json()["wa_url"]
    assert wa_url.startswith("https://wa.me/201001234567?text=Your%20invoice")
    link = unquote(wa_url).split("\n")[-1]
    path = link.split("alpha.localhost", 1)[1]
    assert path.startswith("/shared/")

    anonymous = Client(HTTP_HOST="alpha.localhost")
    pdf = anonymous.get(path)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf["Content-Disposition"].startswith("inline") and pdf["X-Robots-Tag"]
    message = _message(tenant_a)
    assert message.open_count == 1 and message.opened_at

    assert anonymous.get(path[:-3] + "xx/").status_code == 404  # tampered
    assert Client(HTTP_HOST="bravo.localhost").get(path).status_code == 404  # other client
    later = clock.time() + 31 * 24 * 3600
    monkeypatch.setattr(signing.time, "time", lambda: later)
    assert anonymous.get(path).status_code == 404  # ended
    monkeypatch.undo()

    stop = owner.post(f"/api/v1/messages/{message.pk}/stop/", HTTP_IDEMPOTENCY_KEY="stop-1")
    assert stop.status_code == 200
    assert anonymous.get(path).status_code == 404


def test_email_a_statement(chromium, owner, tenant_a, sale):  # noqa: F811
    with tenant_context(tenant_a.id):
        from apps.parties.selectors import parties_with_role

        supplier = parties_with_role("supplier").first()
    assert "data-send-open" in owner.get(f"/suppliers/{supplier.pk}/statement/").content.decode()
    response = _send(owner, doc_type="statement", role="supplier", pk=supplier.pk,
                     date_from="2026-01-01", channel="email", to="supplier@example.com")
    assert response.status_code == 201, response.content
    run_pending()
    filename, content, _kind = mail.outbox[0].attachments[0]
    assert content.startswith(b"%PDF-") and supplier.name in filename
    assert _send(owner, doc_type="statement", role="partner", pk=supplier.pk, channel="email",
                 to="a@b.co").status_code == 404


def test_the_feature_can_be_switched_off(owner, tenant_a, sale):  # noqa: F811
    from apps.platform.tenants import features
    from apps.platform.tenants.models import TenantFeature

    TenantFeature.objects.create(tenant=tenant_a, key="messaging", enabled=False)
    features.forget(tenant_a.id)
    assert _send(owner, doc_type="sales_invoice", pk=sale.pk, channel="email",
                 to="a@b.co").status_code == 403
    assert "data-send-open" not in owner.get(f"/sales/{sale.pk}/").content.decode()


def test_daily_reminders(tenant_a, make_member, monkeypatch):
    from apps.messaging.jobs import daily_digest
    from apps.org import dashboard

    monkeypatch.setattr(dashboard, "needs_attention", lambda actor: [
        dashboard.Attention("Cheques due to collect", 2, "/cheques/?state=due", "receipt")])
    manager = make_member(tenant_a, "manager", "manager")
    make_member(tenant_a, "viewer", "viewer")
    owner_client = login(tenant_a)
    response = owner_client.put("/api/v1/messages/digest/", {"daily": [manager.pk]},
                                content_type="application/json")
    assert response.status_code == 200
    viewer = login(tenant_a, username="viewer")
    assert viewer.put("/api/v1/messages/digest/", {"daily": []},
                      content_type="application/json").status_code == 403

    with tenant_context(tenant_a.id):
        assert list(DigestRecipient.objects.values_list("membership_id", flat=True)) == [
            manager.pk]
        daily_digest()
    run_pending()
    assert [m.to for m in mail.outbox] == [["manager@alpha.test"]]
    html = mail.outbox[0].alternatives[0][0]
    assert "Cheques due to collect" in html and "alpha.localhost" in html
    assert "/cheques/?state=due" in mail.outbox[0].body

    monkeypatch.setattr(dashboard, "needs_attention", lambda actor: [])
    with tenant_context(tenant_a.id):
        daily_digest()  # nothing to say: no e-mail
    assert run_pending() == 0 and len(mail.outbox) == 1


# ---- the monthly report ----------------------------------------------------------------------

def test_previous_period_is_whole_months_when_the_period_is():
    from datetime import date

    from apps.reports.statements import previous_period

    assert previous_period(date(2026, 9, 1), date(2026, 9, 30)) == (date(2026, 8, 1),
                                                                    date(2026, 8, 31))
    assert previous_period(date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 2, 1),
                                                                    date(2026, 2, 28))
    assert previous_period(date(2026, 1, 1), date(2026, 3, 31)) == (date(2025, 10, 1),
                                                                    date(2025, 12, 31))
    assert previous_period(date(2026, 9, 10), date(2026, 9, 19)) == (date(2026, 8, 31),
                                                                     date(2026, 9, 9))


def test_the_summary_matches_the_profit_and_loss(tenant_a, sale):  # noqa: F811
    from django.utils import timezone, translation

    from apps.org.models import Branch
    from apps.reports.registry import REPORTS
    from apps.reports.statements import pnl_summary

    today = timezone.localdate()
    with tenant_context(tenant_a.id), translation.override("en"):
        summary = pnl_summary(today.replace(day=1), today, None)
        report = REPORTS["profit_loss"]()
        params = report.parse({"columns": "period"}, Branch.objects.all())
        rows = {row["label"]: row for row in report.run(params, None)[0].rows}
    assert summary["sales"] == rows["Total sales"]["amount"] > 0
    assert summary["gross"] == rows["Gross profit"]["amount"]
    assert summary["net"] == rows["Net profit"]["amount"]


def _this_month():
    from django.utils import timezone

    today = timezone.localdate()
    return today.year, today.month


def test_monthly_report_email(chromium, owner, tenant_a, sale):  # noqa: F811
    from django.utils import translation

    from apps.iam.models import Membership
    from apps.messaging.jobs import queue_monthly

    with tenant_context(tenant_a.id), translation.override("en"):
        membership = Membership.objects.select_related("user").get(username="owner")
        message = queue_monthly(membership, *_this_month())
    assert message is not None and message.kind == "monthly"
    run_pending()
    sent = mail.outbox[0]
    assert sent.to == ["owner@alpha.test"] and "Monthly report" in sent.subject
    html = sent.alternatives[0][0]
    assert "Net profit" in html and "Gross profit" in html and "Close the month" in html
    assert "Profit and loss" in sent.body and "1 sales invoice" in sent.body
    names = [name for name, content, kind in sent.attachments]
    assert len(names) == 3 and all(n.endswith(".pdf") for n in names)
    assert names[0].startswith("profit_loss_") and names[1].startswith("balance_sheet_")
    assert all(content.startswith(b"%PDF-") for _n, content, _k in sent.attachments)


def test_monthly_report_follows_each_readers_roles(tenant_a, sale, make_member,  # noqa: F811
                                                   monkeypatch):
    from apps.iam.models import Membership
    from apps.messaging.jobs import queue_monthly
    from apps.platform.tenants import features
    from apps.platform.tenants.models import TenantFeature

    TenantFeature.objects.create(tenant=tenant_a, key="reports", enabled=False)
    features.forget(tenant_a.id)
    from django.utils import translation

    with tenant_context(tenant_a.id), translation.override("en"):
        owner_membership = Membership.objects.select_related("user").get(username="owner")
        message = queue_monthly(owner_membership, *_this_month())
    run_pending()  # reports switched off: no figures, no PDFs, still the sales count
    sent = mail.outbox[0]
    assert sent.attachments == [] and "Net profit" not in sent.alternatives[0][0]
    assert "1 sales invoice" in sent.body and message is not None

    member = make_member(tenant_a, "nobody", "viewer")
    with tenant_context(tenant_a.id):
        from apps.iam.models import MembershipRole

        MembershipRole.objects.filter(membership=member).delete()  # no roles at all
        member = Membership.objects.select_related("user").get(pk=member.pk)
        assert queue_monthly(member, *_this_month()) is None


def test_monthly_recipients_schedule_and_send_now(tenant_a, make_member, monkeypatch):
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    from apps.messaging import jobs as messaging_jobs
    from apps.platform.jobs import registry
    from apps.platform.jobs.worker import Scheduler

    manager = make_member(tenant_a, "manager", "manager")
    owner_client = login(tenant_a)
    response = owner_client.put("/api/v1/messages/digest/",
                                {"daily": [], "monthly": [manager.pk]},
                                content_type="application/json")
    assert response.status_code == 200 and response.json()["monthly"] == [manager.pk]
    with tenant_context(tenant_a.id):
        row = DigestRecipient.objects.get()
    assert (row.membership_id, row.daily, row.monthly) == (manager.pk, False, True)

    queued = []
    monkeypatch.setattr(messaging_jobs, "queue_monthly",
                        lambda membership, year, month, **kw: queued.append(
                            (membership.username, year, month)))
    with tenant_context(tenant_a.id):
        messaging_jobs.monthly_pack()
    assert [name for name, _y, _m in queued] == ["manager"]

    schedule = next(s for s in registry.SCHEDULES if s.task == "messaging.monthly_pack")
    assert (schedule.monthday, schedule.at) == (1, time(8, 0))
    monkeypatch.setattr(registry, "SCHEDULES", [schedule])
    cairo = ZoneInfo("Africa/Cairo")
    assert Scheduler().tick(datetime(2026, 10, 2, 9, 0, tzinfo=cairo)) == 0  # not the 1st
    assert Scheduler().tick(datetime(2026, 11, 1, 9, 0, tzinfo=cairo)) == 1

    monkeypatch.undo()
    now = owner_client.post("/api/v1/messages/monthly/", HTTP_IDEMPOTENCY_KEY="monthly-1")
    assert now.status_code == 201
    with tenant_context(tenant_a.id):
        assert OutboundMessage.objects.get(pk=now.json()["id"]).recipient == "owner@alpha.test"
    viewer = make_member(tenant_a, "viewer", "viewer")
    assert login(tenant_a, username="viewer").post(
        "/api/v1/messages/monthly/", HTTP_IDEMPOTENCY_KEY="monthly-2").status_code == 403
    assert viewer
