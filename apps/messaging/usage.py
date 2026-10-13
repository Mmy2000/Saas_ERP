"""WhatsApp usage (legacy WhatsappDashboard): how many messages went out, by route, this month
and last. Counted from the shop's own log of what it sent (OutboundMessage), inside the
current tenant; the console adds the hosted service's own account figures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from django.db.models import Count, Max
from django.utils import timezone

from .models import Channel, MessageStatus, OutboundMessage, Via

ROUTES = (Via.API, Via.OWN, Via.LINK)


@dataclass
class Usage:
    sent: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ROUTES, 0))
    failed: int = 0
    campaign: int = 0  # campaign messages from the shop's number (not in the log)

    @property
    def total(self) -> int:
        return sum(self.sent.values()) + self.campaign


def month_bounds(today: date | None = None, *, back: int = 0) -> tuple[datetime, datetime]:
    """[start, end) of the month `back` months before `today`'s, as aware datetimes."""
    today = today or timezone.localdate()
    year, month = today.year, today.month - back
    while month < 1:
        year, month = year - 1, month + 12
    first = date(year, month, 1)
    after = date(year + (month == 12), month % 12 + 1, 1)
    zone = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(first, time.min), zone),
            timezone.make_aware(datetime.combine(after, time.min), zone))


def usage(start: datetime, end: datetime) -> Usage:
    """The current tenant's WhatsApp messages created in [start, end)."""
    from .models import CampaignRecipient, RecipientStatus

    found = Usage()
    rows = (OutboundMessage.objects.filter(channel=Channel.WHATSAPP, created_at__gte=start,
                                           created_at__lt=end)
            .values("via", "status").annotate(n=Count("id")))
    for row in rows:
        if row["status"] == MessageStatus.FAILED:
            found.failed += row["n"]
        elif row["via"] in found.sent and row["status"] in (
                MessageStatus.SENT, MessageStatus.SHARED, MessageStatus.STOPPED):
            found.sent[row["via"]] += row["n"]
    found.campaign = CampaignRecipient.objects.filter(
        status=RecipientStatus.SENT, sent_at__gte=start, sent_at__lt=end).count()
    return found


def last_sent() -> datetime | None:
    return OutboundMessage.objects.filter(channel=Channel.WHATSAPP).aggregate(
        last=Max("created_at"))["last"]
