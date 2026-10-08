"""Background tasks: sending an e-mail (with its PDF), and the daily reminders."""

from __future__ import annotations

from datetime import time, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Sum
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l

from apps.core.models import DocStatus
from apps.core.tenancy.context import require_current_tenant_id
from apps.platform.jobs.registry import JobFailed, daily, enqueue, monthly, task

from .models import Channel, DigestRecipient, MessageKind, MessageStatus, OutboundMessage
from .services import company_name, message_pdf, sender_address


def _note_error(payload, error, final):
    message = OutboundMessage.objects.filter(pk=payload.get("message_id")).first()
    if message is not None and message.status == MessageStatus.QUEUED:
        kind, _sep, reason = error.partition(": ")
        if kind in ("JobFailed", "RuntimeError") and reason:
            error = reason  # our own words for the shop, without the exception's name
        message.error = error[:1000]
        if final:
            message.status = MessageStatus.FAILED
        message.save(update_fields=["status", "error", "updated_at"])


@task("messaging.send_email", label=_l("Send an e-mail"), max_attempts=4,
      on_error=_note_error)
def send_email(message_id):
    message = (OutboundMessage.objects.select_related("created_by")
               .filter(pk=message_id).first())
    if (message is None or message.channel != Channel.EMAIL
            or message.status != MessageStatus.QUEUED):
        return  # sent already, or no longer wanted
    company = company_name()
    reply_to = [message.created_by.email] if message.created_by and message.created_by.email \
        else None
    mail = EmailMultiAlternatives(message.subject, message.body, sender_address(company),
                                  [message.recipient], reply_to=reply_to)
    if message.params.get("html"):
        mail.attach_alternative(message.params["html"], "text/html")
    try:
        for content, filename in _attachments(message):
            mail.attach(filename, content, "application/pdf")
    except LookupError as exc:
        raise JobFailed(str(exc)) from exc
    mail.send()
    message.status, message.sent_at, message.error = MessageStatus.SENT, timezone.now(), ""
    message.save(update_fields=["status", "sent_at", "error", "updated_at"])


@task("messaging.send_whatsapp", label=_l("Send a WhatsApp message"), max_attempts=3,
      on_error=_note_error)
def send_whatsapp(message_id):
    """A document or statement through the hosted service or the shop's linked number."""
    from . import gateways
    from .models import Via, WhatsAppLink

    message = OutboundMessage.objects.select_related("party").filter(pk=message_id).first()
    if (message is None or message.channel != Channel.WHATSAPP
            or message.status != MessageStatus.QUEUED or message.via not in (Via.API, Via.OWN)):
        return  # sent already, or no longer wanted
    try:
        pdf, filename = message_pdf(message)
    except LookupError as exc:
        raise JobFailed(str(exc)) from exc
    if message.via == Via.OWN:
        link = WhatsAppLink.objects.first()
        if link is None or not link.connected:
            raise JobFailed(_("The shop's WhatsApp number is not linked. Link it again in "
                              "Settings → WhatsApp."))
        result = gateways.own_send(link.session_id, phone=message.recipient, text=message.body,
                                   pdf=pdf, filename=filename)
    else:
        order_id, amount = _facts(message)
        result = gateways.hosted_send(
            phone=message.recipient, name=message.party.name if message.party else "",
            order_id=order_id, amount=amount, pdf=pdf, filename=filename,
            statement=message.kind == MessageKind.STATEMENT)
    if not result.ok:
        raise RuntimeError(result.error)  # tried again a few times, then "Not sent"
    message.status, message.sent_at, message.error = MessageStatus.SENT, timezone.now(), ""
    if gateways.console():  # say so, or a development "sent" looks like a real one
        message.error = _("Development mode: nothing was really sent.")
    message.save(update_fields=["status", "sent_at", "error", "updated_at"])


@task("messaging.campaign_next", label=_l("Send the next WhatsApp campaign message"),
      max_attempts=3)
def campaign_next(campaign_id, step=0):
    from .campaigns import send_next

    send_next(campaign_id, step)


def _facts(message) -> tuple[str, str]:
    """The document's number and total for the hosted service's wording (a statement sends its
    title and no amount)."""
    from django.apps import apps

    marks = {chr(code) for code in range(0x2066, 0x206A)}  # direction isolates
    plain = "".join(ch for ch in message.doc_label if ch not in marks)
    if message.kind != MessageKind.DOCUMENT or not message.doc_model:
        return plain, "0"
    record = apps.get_model(message.doc_model).objects.filter(pk=message.doc_id).first()
    number = getattr(record, "number", "") or plain
    for name in ("total_amount", "grand_total", "total", "amount"):
        value = getattr(record, name, None)
        if value is not None and not callable(value):
            return number, str(value)
    return number, "0"


def _attachments(message) -> list[tuple[bytes, str]]:
    if message.kind == MessageKind.DIGEST:
        return []
    if message.kind == MessageKind.MONTHLY:
        from .monthly import pack_files

        return pack_files(message)
    return [message_pdf(message)]


# ---- the daily reminders e-mail ------------------------------------------------------------

def workspace_url() -> str:
    """This client's address, for links in e-mails."""
    from apps.platform.tenants.models import TenantDomain

    domain = (TenantDomain.objects.filter(tenant_id=require_current_tenant_id())
              .order_by("-is_primary", "pk").values_list("domain", flat=True).first())
    return f"{settings.SITE_SCHEME}://{domain}{settings.SITE_PORT}" if domain else ""


def yesterday_sales(actor) -> dict | None:
    if not actor.can("sales.invoice.view"):
        return None
    from apps.sales.models import SalesInvoice

    day = timezone.localdate() - timedelta(days=1)
    invoices = SalesInvoice.objects.filter(status=DocStatus.POSTED, business_date=day)
    branches = actor.branch_ids("sales.invoice.view")
    if branches is not None:
        invoices = invoices.filter(branch_id__in=branches)
    totals = invoices.aggregate(count=Count("id"), total=Sum("total_amount"))
    return {"day": day, "count": totals["count"], "total": totals["total"] or 0}


def digest_for(membership) -> dict | None:
    """What one user's e-mail says: their reminders and yesterday's sales, within what their
    roles let them see. None when there is nothing to say."""
    from apps.iam.authz import build_actor
    from apps.org.dashboard import needs_attention

    actor = build_actor(membership)
    items = needs_attention(actor)
    sales = yesterday_sales(actor)
    if not items and not (sales and sales["count"]):
        return None
    base = workspace_url()
    return {"items": [{"label": i.label, "count": i.count, "url": base + i.url} for i in items],
            "sales": sales, "home": base + "/", "company": company_name(),
            "name": membership.user.display_name or membership.username,
            "today": date_format(timezone.localdate(), "DATE_FORMAT")}


@task("messaging.daily_digest", label=_l("Daily reminders e-mails"), max_attempts=2)
def daily_digest():
    from apps.iam.models import MembershipStatus
    from apps.platform.tenants.features import is_enabled

    if not is_enabled(require_current_tenant_id(), "messaging"):
        return
    recipients = DigestRecipient.objects.filter(daily=True).select_related("membership__user")
    for recipient in recipients:
        membership, user = recipient.membership, recipient.membership.user
        if (membership.status != MembershipStatus.ACTIVE or not user.is_active
                or not user.email):
            continue
        content = digest_for(membership)
        if content is None:
            continue
        subject = _("Daily reminders · %(company)s · %(date)s") % {
            "company": content["company"], "date": content["today"]}
        message = OutboundMessage.objects.create(
            channel=Channel.EMAIL, kind=MessageKind.DIGEST, recipient=user.email,
            subject=subject[:300], doc_label=_("Daily reminders"),
            body=render_to_string("messaging/email/digest.txt", content),
            params={"html": render_to_string("messaging/email/digest.html", content)})
        enqueue("messaging.send_email", message_id=message.pk)


daily("messaging.daily_digest", at=time(7, 30))


# ---- the monthly report e-mail -------------------------------------------------------------

def queue_monthly(membership, year: int, month: int, *, user=None) -> OutboundMessage | None:
    """Queue one person's monthly report (None: no e-mail address, or nothing they may see)."""
    from .monthly import pack_for

    if not membership.user.email:
        return None
    content = pack_for(membership, year, month)
    if content is None:
        return None
    subject = _("Monthly report · %(company)s · %(month)s") % {
        "company": content["company"], "month": content["month"]}
    message = OutboundMessage.objects.create(
        channel=Channel.EMAIL, kind=MessageKind.MONTHLY, recipient=membership.user.email,
        subject=subject[:300], doc_label=_("Monthly report") + f" · {content['month']}",
        body=render_to_string("messaging/email/monthly.txt", content), created_by=user,
        params={"membership": membership.pk, "year": year, "month": month,
                "html": render_to_string("messaging/email/monthly.html", content)})
    enqueue("messaging.send_email", message_id=message.pk)
    return message


@task("messaging.monthly_pack", label=_l("Monthly report e-mails"), max_attempts=2)
def monthly_pack():
    from apps.iam.models import MembershipStatus
    from apps.platform.tenants.features import is_enabled

    from .monthly import last_month

    if not is_enabled(require_current_tenant_id(), "messaging"):
        return
    year, month = last_month()
    for recipient in DigestRecipient.objects.filter(monthly=True).select_related(
            "membership__user"):
        membership = recipient.membership
        if membership.status == MembershipStatus.ACTIVE and membership.user.is_active:
            queue_monthly(membership, year, month)


monthly("messaging.monthly_pack", day=1, at=time(8, 0))
