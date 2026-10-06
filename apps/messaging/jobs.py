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
from apps.platform.jobs.registry import JobFailed, daily, enqueue, task

from .models import Channel, DigestRecipient, MessageKind, MessageStatus, OutboundMessage
from .services import company_name, message_pdf, sender_address


def _note_error(payload, error, final):
    message = OutboundMessage.objects.filter(pk=payload.get("message_id")).first()
    if message is not None and message.status == MessageStatus.QUEUED:
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
    if message.kind == MessageKind.DIGEST:
        mail.attach_alternative(message.params.get("html", ""), "text/html")
    else:
        try:
            content, filename = message_pdf(message)
        except LookupError as exc:
            raise JobFailed(str(exc)) from exc
        mail.attach(filename, content, "application/pdf")
    mail.send()
    message.status, message.sent_at, message.error = MessageStatus.SENT, timezone.now(), ""
    message.save(update_fields=["status", "sent_at", "error", "updated_at"])


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
    recipients = DigestRecipient.objects.select_related("membership__user")
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
