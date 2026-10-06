"""Messages the shop sends: documents and statements as PDFs by e-mail or as a WhatsApp link,
and the daily reminders e-mail. Each row is also the log the shop sees (Settings → Sent
messages). E-mails go out from the background worker (apps.platform.jobs)."""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class Channel(models.TextChoices):
    EMAIL = "email", _("E-mail")
    WHATSAPP = "whatsapp", _("WhatsApp")


class MessageKind(models.TextChoices):
    DOCUMENT = "document", _("Document")
    STATEMENT = "statement", _("Statement")
    DIGEST = "digest", _("Daily reminders")


class MessageStatus(models.TextChoices):
    QUEUED = "queued", _("Waiting to send")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Not sent")
    SHARED = "shared", _("Link shared")
    STOPPED = "stopped", _("Link stopped")


class OutboundMessage(TenantScopedModel):
    channel = models.CharField(max_length=10, choices=Channel.choices)
    kind = models.CharField(max_length=10, choices=MessageKind.choices)
    status = models.CharField(max_length=10, choices=MessageStatus.choices,
                              default=MessageStatus.QUEUED)
    recipient = models.CharField(max_length=254)  # an e-mail address, or a phone for WhatsApp
    subject = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    # What is sent: a printed document type (apps.printing.documents.TYPES) or a party role
    # for a statement; the record's model ("sales.SalesInvoice") links the log to its page.
    doc_type = models.CharField(max_length=40, blank=True)
    doc_model = models.CharField(max_length=80, blank=True)
    doc_id = models.PositiveBigIntegerField(null=True, blank=True)
    doc_label = models.CharField(max_length=200, blank=True)
    params = models.JSONField(default=dict, blank=True)  # statement period; the digest's HTML
    party = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)  # a shared link, first opened
    open_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "-created_at"], name="messaging_recent_idx"),
            models.Index(fields=["tenant", "doc_model", "doc_id"], name="messaging_doc_idx"),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} → {self.recipient}"


class DigestRecipient(TenantScopedModel):
    """A user who gets the daily reminders e-mail (chosen by the owner on Sent messages)."""

    membership = models.ForeignKey("iam.Membership", on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "membership"],
                                    name="messaging_digest_member_uniq"),
        ]
