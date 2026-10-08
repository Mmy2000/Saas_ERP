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
    MONTHLY = "monthly", _("Monthly report")
    TEST = "test", _("Test message")


class Via(models.TextChoices):
    """How a WhatsApp message went: the shop's WhatsApp opened with a link, the hosted service,
    or the shop's own linked number (apps.messaging.gateways)."""

    LINK = "link", _("WhatsApp link")
    API = "api", _("WhatsApp service")
    OWN = "own", _("Shop's number")


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
    via = models.CharField(max_length=8, choices=Via.choices, blank=True)  # WhatsApp only
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
    """A user who gets the daily reminders and/or the monthly report e-mail (chosen by the
    owner on Sent messages)."""

    membership = models.ForeignKey("iam.Membership", on_delete=models.CASCADE, related_name="+")
    daily = models.BooleanField(default=True)
    monthly = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "membership"],
                                    name="messaging_digest_member_uniq"),
        ]


class LinkStatus(models.TextChoices):
    NOT_STARTED = "not_started", _("Not linked")
    STARTING = "starting", _("Starting")
    QR = "qr", _("Waiting for the scan")
    CONNECTED = "connected", _("Linked")
    DISCONNECTED = "disconnected", _("Disconnected")


class WhatsAppLink(TenantScopedModel):
    """The shop's own WhatsApp number on the platform's gateway: one session per client, linked
    by scanning a QR in Settings → WhatsApp (legacy whatsapp_gateway WhatsappSender, which also
    allowed several numbers; one is enough here)."""

    session_id = models.CharField(max_length=80)
    status = models.CharField(max_length=14, choices=LinkStatus.choices,
                              default=LinkStatus.NOT_STARTED)
    phone = models.CharField(max_length=20, blank=True)
    linked_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="messaging_wa_link_uniq")]

    @property
    def connected(self) -> bool:
        return self.status == LinkStatus.CONNECTED


class CampaignStatus(models.TextChoices):
    RUNNING = "running", _("Sending")
    PAUSED = "paused", _("Paused")
    DONE = "done", _("Finished")
    CANCELLED = "cancelled", _("Cancelled")


class Campaign(TenantScopedModel):
    """One WhatsApp message to many customers from the shop's linked number (legacy
    whatsapp_gateway BulkCampaign). Sent one by one by the worker, slowly and at random gaps,
    with a daily cap, so WhatsApp does not take the shop for a spammer."""

    name = models.CharField(max_length=120)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=CampaignStatus.choices,
                              default=CampaignStatus.RUNNING)
    audience = models.JSONField(default=dict, blank=True)  # the choice it was built from
    total = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    daily_cap = models.PositiveSmallIntegerField(default=150)
    min_gap = models.PositiveSmallIntegerField(default=25)  # seconds between two messages
    max_gap = models.PositiveSmallIntegerField(default=70)
    failures_in_a_row = models.PositiveSmallIntegerField(default=0)
    # Each queued send carries the step it was queued at; a stale one (paused and resumed in
    # between) finds a newer step and stops, so one campaign never sends from two chains.
    step = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=300, blank=True)  # why it paused, or stopped
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "status"], name="messaging_campaign_idx")]

    def __str__(self):
        return self.name

    @property
    def left(self) -> int:
        return max(self.total - self.sent - self.failed - self.skipped, 0)

    @property
    def percent(self) -> int:
        return round((self.total - self.left) * 100 / self.total) if self.total else 100


class RecipientStatus(models.TextChoices):
    QUEUED = "queued", _("Waiting")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Not sent")
    SKIPPED = "skipped", _("Skipped")


class CampaignRecipient(TenantScopedModel):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients")
    party = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20)  # digits with the country code
    status = models.CharField(max_length=8, choices=RecipientStatus.choices,
                              default=RecipientStatus.QUEUED)
    reason = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(  # skipped rows may repeat a number (why skipped)
            fields=["tenant", "campaign", "phone"], condition=~models.Q(status="skipped"),
            name="messaging_recipient_uniq")]
        indexes = [models.Index(fields=["tenant", "campaign", "status"],
                                name="messaging_recipient_idx")]


class OptOut(TenantScopedModel):
    """A number that asked not to get campaigns (Gweb had the table but no screen for it)."""

    phone = models.CharField(max_length=20)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["tenant", "phone"],
                                               name="messaging_optout_uniq")]
