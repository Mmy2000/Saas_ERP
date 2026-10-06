"""Sending documents and statements to customers.

* By e-mail: the message is logged and a job is queued in the same transaction; the worker
  makes the PDF and sends it (apps.messaging.jobs). The shop sees it as "Waiting to send", then
  "Sent" or "Not sent" with the reason.
* On WhatsApp: there is no API to set up. The shop's own WhatsApp opens (wa.me) with the text
  and a private link to the PDF; the link is signed, ends after SHARE_DAYS and can be stopped.

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

from .models import Channel, MessageKind, MessageStatus, OutboundMessage

# Numbers and names inside a sentence of the other script keep their own direction: an
# invoice number in an Arabic sentence is wrapped in LRI...PDI so it does not read backwards.
LTR = "\u2066{}\u2069"
ISOLATE = "\u2068{}\u2069"

SHARE_DAYS = 30
SHARE_SALT = "messaging.share"
PARTY_FIELDS = ("customer", "trade_account", "supplier", "party")
STATEMENT_ROLES = ("customer", "supplier", "trade_account", "workshop")


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
    sent.sent_at = timezone.now()
    sent.save(update_fields=["sent_at"])
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
    if message.channel != Channel.EMAIL or message.status != MessageStatus.FAILED:
        raise DomainError(_("Only an e-mail that was not sent can be sent again."),
                          code="MESSAGING_NOT_FAILED")
    message.status, message.error = MessageStatus.QUEUED, ""
    message.save(update_fields=["status", "error", "updated_at"])
    enqueue("messaging.send_email", message_id=message.pk)


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
