"""Sending documents and statements to customers.

* By e-mail: the message is logged and a job is queued in the same transaction; the worker
  makes the PDF and sends it (apps.messaging.jobs). The shop sees it as "Waiting to send", then
  "Sent" or "Not sent" with the reason.
* On WhatsApp, one of three routes (`whatsapp_routes`): the shop's own WhatsApp opens (wa.me)
  with the text and a private link to the PDF (signed, ends after SHARE_DAYS, can be stopped);
  or, when the platform switched them on for the client, the PDF goes out from the background
  worker through the hosted WhatsApp service or from the shop's own linked number
  (apps.messaging.gateways), logged like an e-mail.

Who may send what: the same permission and branch scope as opening or printing the record,
plus `messaging.send`.
"""

from __future__ import annotations

from datetime import date
from email.utils import formataddr, parseaddr
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, PermissionDenied, ValidationError
from apps.core.tenancy.context import require_current_tenant_id
from apps.org.models import TenantProfile
from apps.platform.jobs.registry import enqueue

from . import gateways
from .models import (
    Channel,
    LinkStatus,
    MessageKind,
    MessageStatus,
    OutboundMessage,
    Via,
    WhatsAppLink,
)

# Numbers and names inside a sentence of the other script keep their own direction: an
# invoice number in an Arabic sentence is wrapped in LRI...PDI so it does not read backwards.
LTR = "\u2066{}\u2069"
ISOLATE = "\u2068{}\u2069"

SHARE_DAYS = 30
SHARE_SALT = "messaging.share"
PARTY_FIELDS = ("customer", "trade_account", "supplier", "party")
STATEMENT_ROLES = ("customer", "supplier", "trade_account", "workshop", "partner")


# ---- what is being sent --------------------------------------------------------------------

class Target:
    """A document or a statement the actor may send: its label, its party and how to find it
    again from the worker."""

    def __init__(self, *, kind, doc_type, label, party=None, model="", pk=None, params=None):
        self.kind, self.doc_type, self.label = kind, doc_type, label
        self.party, self.model, self.pk, self.params = party, model, pk, params or {}


def document_target(request, doc_type: str, pk: int) -> Target:
    from apps.printing.documents import TYPES
    from apps.printing.render import design_for

    kind = TYPES.get(doc_type)
    if kind is None:
        raise NotFound(_("Unknown document type."))
    if not request.actor.can(kind.permission):
        raise PermissionDenied(_("You don't have permission to do that."))
    queryset = kind.finder(request)
    record = queryset.filter(pk=pk).first()
    if record is None:
        raise NotFound(_("Not found."))
    doc = kind.builder(record, design_for(doc_type), {"can_cost": False})
    label = f"{doc.title} {LTR.format(doc.number)}" if doc.number else str(doc.title)
    return Target(kind=MessageKind.DOCUMENT, doc_type=doc_type, label=label,
                  party=_party_of(record), model=queryset.model._meta.label, pk=record.pk)


def statement_target(request, role: str, pk: int, date_from: date | None,
                     date_to: date | None) -> Target:
    from apps.parties.selectors import parties_with_role

    if role not in STATEMENT_ROLES:
        raise NotFound(_("Unknown statement."))
    if not request.actor.can(f"parties.{role}.view"):
        raise PermissionDenied(_("You don't have permission to do that."))
    party = parties_with_role(role).filter(pk=pk).first()
    if party is None:
        raise NotFound(_("Not found."))
    period = {"from": date_from.isoformat() if date_from else None,
              "to": date_to.isoformat() if date_to else None}
    return Target(kind=MessageKind.STATEMENT, doc_type=role, party=party, pk=party.pk,
                  label=_("Statement of account") + " · " + ISOLATE.format(party.name),
                  model="parties.Party",
                  params=period)


def _party_of(record):
    for name in PARTY_FIELDS:
        party = getattr(record, name, None)
        if party is not None and hasattr(party, "email"):
            return party
    return None


def contact(party) -> dict:
    """Where the party can be reached: {"email", "phone"} (either may be "")."""
    if party is None:
        return {"email": "", "phone": ""}
    return {"email": party.email or "", "phone": party.phone_e164 or party.phone or ""}


def company_name() -> str:
    return TenantProfile.objects.values_list("display_name", flat=True).first() or ""


def default_text(target: Target, channel: str) -> str:
    """The message the dialog starts with, in the current language."""
    name = target.party.name if target.party is not None else ""
    values = {"name": ISOLATE.format(name) if name else "", "document": target.label,
              "company": ISOLATE.format(company_name())}
    if channel == Channel.WHATSAPP:
        return (_("Hello %(name)s, here is your %(document)s from %(company)s.") if name
                else _("Here is your %(document)s from %(company)s.")) % values
    return (_("Dear %(name)s,\n\nPlease find attached your %(document)s.\n\nThank you,\n"
              "%(company)s") if name else
            _("Hello,\n\nPlease find attached your %(document)s.\n\nThank you,\n%(company)s")
            ) % values


# ---- sending ---------------------------------------------------------------------------------

def _log(target: Target, *, user, channel, recipient, body, subject="", status):
    return OutboundMessage.objects.create(
        channel=channel, kind=target.kind, status=status, recipient=recipient,
        subject=subject[:300], body=body, doc_type=target.doc_type, doc_model=target.model,
        doc_id=target.pk, doc_label=target.label[:200], params=target.params,
        party=target.party, created_by=user)


def email_target(target: Target, *, user, to: str, message: str) -> OutboundMessage:
    to = (to or "").strip()
    try:
        validate_email(to)
    except Exception as exc:
        raise ValidationError(_("Enter a valid e-mail address."),
                              fields={"to": [_("Enter a valid e-mail address.")]}) from exc
    subject = f"{target.label} · {ISOLATE.format(company_name())}"
    sent = _log(target, user=user, channel=Channel.EMAIL, recipient=to, subject=subject,
                body=message.strip() or default_text(target, Channel.EMAIL),
                status=MessageStatus.QUEUED)
    enqueue("messaging.send_email", message_id=sent.pk)
    return sent


def whatsapp_target(target: Target, *, user, phone: str, message: str,
                    base_url: str) -> tuple[OutboundMessage, str]:
    """Log the share and return the wa.me address that opens the shop's WhatsApp with the
    text and the document's private link. `base_url` is this workspace's address."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    text = message.strip() or default_text(target, Channel.WHATSAPP)
    sent = _log(target, user=user, channel=Channel.WHATSAPP, recipient=digits, body=text,
                status=MessageStatus.SHARED)
    sent.sent_at, sent.via = timezone.now(), Via.LINK
    sent.save(update_fields=["sent_at", "via"])
    link = base_url.rstrip("/") + share_path(sent)
    return sent, f"https://wa.me/{digits}?text={quote(text + chr(10) + link)}"


def share_path(message: OutboundMessage) -> str:
    from django.urls import reverse

    return reverse("shared-document", args=[share_token(message)])


def share_token(message: OutboundMessage) -> str:
    return signing.dumps({"m": message.pk, "t": message.tenant_id}, salt=SHARE_SALT,
                         compress=True)


def shared_message(token: str) -> OutboundMessage | None:
    """The shared message behind a link on this workspace, if the link is still good."""
    try:
        data = signing.loads(token, salt=SHARE_SALT, max_age=SHARE_DAYS * 24 * 3600)
    except signing.BadSignature:
        return None
    if data.get("t") != require_current_tenant_id():
        return None
    return OutboundMessage.objects.filter(pk=data.get("m"), channel=Channel.WHATSAPP,
                                          status=MessageStatus.SHARED).first()


def stop_link(message: OutboundMessage) -> None:
    if message.channel != Channel.WHATSAPP or message.status != MessageStatus.SHARED:
        raise DomainError(_("Only a shared link can be stopped."), code="MESSAGING_NOT_LINK")
    message.status = MessageStatus.STOPPED
    message.save(update_fields=["status", "updated_at"])


def send_again(message: OutboundMessage) -> None:
    through_worker = message.channel == Channel.EMAIL or message.via in (Via.API, Via.OWN)
    if (not through_worker or message.status != MessageStatus.FAILED
            or message.kind == MessageKind.TEST):
        raise DomainError(_("Only a message that was not sent can be sent again."),
                          code="MESSAGING_NOT_FAILED")
    message.status, message.error = MessageStatus.QUEUED, ""
    message.save(update_fields=["status", "error", "updated_at"])
    enqueue("messaging.send_email" if message.channel == Channel.EMAIL
            else "messaging.send_whatsapp", message_id=message.pk)


# ---- WhatsApp through a gateway --------------------------------------------------------------

def whatsapp_link() -> WhatsAppLink | None:
    return WhatsAppLink.objects.first()


def whatsapp_routes(actor) -> list[str]:
    """How this member can send on WhatsApp now, the preferred first: the shop's linked number,
    the hosted service, and always the link that opens their own WhatsApp."""
    routes = []
    if actor.can("messaging.whatsapp.own") and gateways.own_ready():
        link = whatsapp_link()
        if link is not None and link.connected:
            routes.append(Via.OWN)
    if actor.can("messaging.whatsapp.api") and gateways.hosted_ready():
        routes.append(Via.API)
    routes.append(Via.LINK)
    return routes


def whatsapp_send(target: Target, *, actor, phone: str, message: str, via: str
                  ) -> OutboundMessage:
    """Queue the PDF for the worker to send through a gateway (`via` = api or own)."""
    if via not in whatsapp_routes(actor) or via == Via.LINK:
        raise ValidationError(_("This way of sending is not available."),
                              fields={"via": [_("This way of sending is not available.")]})
    digits = gateways.phone_digits(phone)
    if not digits:
        raise ValidationError(_("Enter the WhatsApp number with the country code."),
                              fields={"to": [_("Enter the WhatsApp number with the country "
                                               "code.")]})
    text = message.strip() or default_text(target, Channel.WHATSAPP)
    sent = _log(target, user=actor.user, channel=Channel.WHATSAPP, recipient=digits, body=text,
                status=MessageStatus.QUEUED)
    sent.via = via
    sent.save(update_fields=["via"])
    enqueue("messaging.send_whatsapp", message_id=sent.pk)
    return sent


def link_number(tenant_slug: str) -> WhatsAppLink:
    """Start (or restart) the shop's session on the gateway; the QR follows in `check_link`."""
    link = whatsapp_link() or WhatsAppLink(session_id=gateways.new_session_id(tenant_slug))
    result = gateways.start(link.session_id)
    link.status = LinkStatus.STARTING if result.ok else link.status
    link.error = "" if result.ok else result.error[:300]
    link.checked_at = timezone.now()
    link.save()
    if not result.ok:
        raise DomainError(result.error, code="MESSAGING_GATEWAY")
    return link


def check_link() -> tuple[WhatsAppLink | None, str]:
    """Ask the gateway how the session is: (the link, the QR image while waiting)."""
    link = whatsapp_link()
    if link is None:
        return None, ""
    result = gateways.state(link.session_id)
    link.checked_at = timezone.now()
    if not result.ok:
        link.error = result.error[:300]
        link.save(update_fields=["error", "checked_at", "updated_at"])
        return link, ""
    status = result.data.get("status", LinkStatus.DISCONNECTED)
    if status == LinkStatus.CONNECTED and not link.connected:
        link.linked_at = timezone.now()
    link.status, link.error = status, ""
    # The number only while linked (a stale one, e.g. from the development stand-in, goes).
    link.phone = (result.data.get("phone") or link.phone) if status == LinkStatus.CONNECTED else ""
    link.save()
    return link, result.data.get("qr", "") if status == LinkStatus.QR else ""


def unlink_number() -> None:
    """Log the number out on the gateway and forget the session; linking again starts a new
    one (and a new QR). Forgotten here even when the gateway cannot be reached."""
    link = whatsapp_link()
    if link is not None:
        gateways.logout(link.session_id)
    WhatsAppLink.objects.all().delete()


def send_test(*, actor, phone: str) -> OutboundMessage:
    """A short message from the linked number, sent at once so the shop sees it work."""
    link = whatsapp_link()
    if link is None or not link.connected:
        raise DomainError(_("Link the shop's WhatsApp number first."), code="MESSAGING_NOT_LINKED")
    digits = gateways.phone_digits(phone)
    if not digits:
        raise ValidationError(_("Enter the WhatsApp number with the country code."),
                              fields={"to": [_("Enter the WhatsApp number with the country "
                                               "code.")]})
    text = _("This is a test message from %(company)s.") % {
        "company": ISOLATE.format(company_name())}
    result = gateways.own_send(link.session_id, phone=digits, text=text)
    return OutboundMessage.objects.create(
        channel=Channel.WHATSAPP, kind=MessageKind.TEST, via=Via.OWN, recipient=digits,
        body=text, created_by=actor.user,
        status=MessageStatus.SENT if result.ok else MessageStatus.FAILED,
        sent_at=timezone.now() if result.ok else None,
        error=(_("Development mode: nothing was really sent.") if result.ok and gateways.console()
               else result.error[:1000]))


# ---- the PDF of a message (worker and shared links) ----------------------------------------

def message_pdf(message: OutboundMessage) -> tuple[bytes, str]:
    """Rebuilt from the record as it is now (a cancelled invoice shows its stamp)."""
    from django.apps import apps

    if message.kind == MessageKind.DOCUMENT:
        from apps.printing.render import record_pdf

        record = apps.get_model(message.doc_model).objects.filter(pk=message.doc_id).first()
        if record is None:
            raise LookupError("The document no longer exists.")
        return record_pdf(message.doc_type, record)
    if message.kind == MessageKind.STATEMENT:
        from apps.parties.selectors import parties_with_role
        from apps.parties.web.views import statement_sheet
        from apps.printing.pdf import sheet_pdf

        party = parties_with_role(message.doc_type).filter(pk=message.doc_id).first()
        if party is None:
            raise LookupError("The party no longer exists.")
        period = message.params or {}
        sheet = statement_sheet(message.doc_type, party, _date(period.get("from")),
                                _date(period.get("to")))
        return (sheet_pdf(sheet["template"], sheet["context"], title=sheet["title"]),
                sheet["filename"])
    raise LookupError("Nothing to attach.")


def _date(value):
    return date.fromisoformat(value) if value else None


def sender_address(company: str) -> str:
    """The platform's address, shown with the shop's name."""
    _name, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    return formataddr((company, address or settings.DEFAULT_FROM_EMAIL)) if company else \
        settings.DEFAULT_FROM_EMAIL
